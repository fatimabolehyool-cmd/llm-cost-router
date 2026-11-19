PY ?= .venv/bin/python
MODELS ?= gemma3:4b qwen3:4b phi4-mini llama3.2:3b
# Mid-size screen (24 Oct): non-thinking instruct models from 4 labs, ~27 GB download.
# qwen3 was dropped: it reasons out loud and never reached an answer within the token limit.
MODELS_MID ?= gemma3:12b llama3.1:8b qwen2.5:7b phi4:14b

# Preflight checks: fail with one clear sentence instead of a stack trace.
DOCKER_UP = @docker info >/dev/null 2>&1 || { echo "Docker is not running. Start Docker Desktop and wait until it shows 'Engine running'."; exit 1; }
OLLAMA_UP = @curl -sf http://localhost:11434/api/version >/dev/null || { echo "Ollama is not reachable on localhost:11434. Start it with 'ollama serve' or the Ollama app."; exit 1; }

.PHONY: venv test lint format cov audit test-integration backup restore-test retention monitoring-up eval-seniors eval-report train registry promote rollback real-up real-ask real-down senior-smoke e4 replay-prepare replay-juniors replay-assemble e3 pull data exp1-screen exp1-rescore pull-mid exp1-screen-mid exp1-load exp2-build exp2-train exp2-up exp2-bench exp2-stats exp2-down env s1 s2 s3 p1 category-report down

venv:            ## create the host virtual environment
	python3 -m venv .venv && .venv/bin/pip install -q -r requirements-host.txt -r requirements-dev.txt
	mkdir -p results && .venv/bin/pip freeze > results/host-env.txt

test:            ## all unit tests (normalizer, routing rules, privacy, breaker)
	$(PY) -m pytest -q tests

lint:            ## the same lint and format checks CI runs
	$(PY) -m ruff check . && $(PY) -m ruff format --check .

format:          ## apply the formatter and safe lint fixes
	$(PY) -m ruff check --fix . && $(PY) -m ruff format .

cov:             ## unit tests with coverage (fails below the floor in pyproject.toml)
	$(PY) -m pytest --cov --cov-report=term-missing

audit:           ## secret scan + dependency audit, as in CI (needs `brew install gitleaks`)
	gitleaks git --no-banner --redact --config .gitleaks.toml .
	$(PY) -m pip_audit --no-deps --disable-pip -r infra/requirements-router.lock
	$(PY) -m pip_audit --no-deps --disable-pip -r infra/requirements-mlops.lock
	$(PY) -m pip_audit --no-deps --disable-pip -r requirements-dev.txt

pull:            ## download the candidate junior models into Ollama
	for m in $(MODELS); do ollama pull $$m; done

data:            ## 100 choice + 100 label questions, fixed seed, checksummed
	$(PY) exp1_juniors/prepare_data.py

exp1-screen:     ## Experiment 1a: every model on every question, every pair scored
	$(PY) exp1_juniors/run_exp1.py screen --models $(MODELS)

exp1-rescore:    ## re-read the 22 Oct raw results with the fixed normalizer (no models needed)
	$(PY) exp1_juniors/run_exp1.py rescore $$(ls results/exp1_screen_raw_*.jsonl | head -1)

pull-mid:        ## download the mid-size candidates (~27 GB)
	for m in $(MODELS_MID); do ollama pull $$m; done

exp1-screen-mid: ## Experiment 1a again with the mid-size models (~40-60 min)
	$(PY) exp1_juniors/run_exp1.py screen --models $(MODELS_MID)

exp1-load:       ## Experiment 1b: usage `make exp1-load PAIR="gemma3:4b qwen3:4b"`
	$(PY) exp1_juniors/run_exp1.py load --pair $(PAIR) --levels 1 4 8 --n 24

exp2-build:
	docker compose -f docker-compose.exp2.yml build

exp2-train:      ## train placeholder policy inside the serving image, log to MLflow
	docker compose -f docker-compose.exp2.yml --profile train run --rm policy-train

exp2-up:         ## start the policy server and time how long until it is healthy
	@start=$$(date +%s); docker compose -f docker-compose.exp2.yml up -d policy-server; \
	until [ "$$(docker inspect -f '{{.State.Health.Status}}' $$(docker compose -f docker-compose.exp2.yml ps -q policy-server))" = "healthy" ]; do sleep 1; done; \
	echo "policy-server healthy after $$(( $$(date +%s) - start )) s (includes container start)" | tee results/exp2_startup.txt

exp2-bench:      ## Experiment 2: measure the router -> policy hop
	$(PY) exp2_policy_serving/bench_policy.py --url http://localhost:8090 --n 500

exp2-stats:      ## memory/CPU of the running policy server
	docker stats --no-stream $$(docker compose -f docker-compose.exp2.yml ps -q policy-server) | tee results/exp2_docker_stats.txt

exp2-down:
	docker compose -f docker-compose.exp2.yml down

# ---------------- policy lifecycle: train + gate through Airflow -> MLflow ----------------
# Only Airflow (and its dependencies) is started here: a bare `up` would also recreate the router
# with the mock settings and undo `make real-up`.
LIFECYCLE = docker compose --profile orchestration
FEATURES ?= v2

train: env       ## train and gate the policy via the Airflow DAG (FEATURES=v1|v2); registers a challenger
	$(DOCKER_UP)
	$(LIFECYCLE) up -d --wait --build airflow
	$(LIFECYCLE) exec -T -e GIT_COMMIT=$$(git rev-parse --short HEAD) airflow \
		airflow dags test revalidate_policy $$(date +%Y-%m-%d) -c '{"features": "$(FEATURES)"}'
	@cat results/revalidate_$$(date +%Y-%m-%d)_$(FEATURES).json

REGISTRY = $(LIFECYCLE) exec -T -w /opt/project airflow /opt/pipelines-venv/bin/python -m pipelines.registry

registry:        ## which MLflow versions are champion / challenger / previous champion
	$(DOCKER_UP)
	$(LIFECYCLE) up -d --wait airflow
	$(REGISTRY) show

promote:         ## make promote VERSION=3 (refused unless it passed the gate); exports it for serving
	@[ -n "$(VERSION)" ] || { echo "Usage: make promote VERSION=<registry version>"; exit 1; }
	$(DOCKER_UP)
	$(LIFECYCLE) up -d --wait airflow
	$(REGISTRY) promote --version $(VERSION)

rollback:        ## champion -> previous champion; exports it for serving
	$(DOCKER_UP)
	$(LIFECYCLE) up -d --wait airflow
	$(REGISTRY) rollback

shadow:          ## offline shadow report: challenger vs the serving champion on the release split
	$(DOCKER_UP)
	$(LIFECYCLE) up -d --wait airflow
	$(LIFECYCLE) exec -T -w /opt/project airflow /opt/pipelines-venv/bin/python -m pipelines.shadow --data data/replay/pool_v1.jsonl

reload:          ## hot reload only: MLServer loads the exported champion, then the router switches
	$(DOCKER_UP)
	$(LIFECYCLE) up -d --wait airflow
	$(REGISTRY) activate

lifecycle-demo: env  ## gate refusal -> train -> shadow -> promote -> rollback, live, no restarts (after real-up)
	$(DOCKER_UP)
	$(OLLAMA_UP)
	$(PY) smoke/lifecycle_demo.py $(FEATURES)

admin-key:       ## create or rotate the admin key in .env (not printed); apply with `make real-up`
	@[ -f .env ] || { echo "no .env yet: run 'make env' first"; exit 1; }
	@k=$$(openssl rand -hex 24); h=$$(printf '%s' "$$k" | shasum -a 256 | cut -d' ' -f1); \
	grep -v '^ROUTER_ADMIN_KEY' .env > .env.tmp; \
	printf 'ROUTER_ADMIN_KEY=%s\nROUTER_ADMIN_KEY_SHA256=%s\n' "$$k" "$$h" >> .env.tmp; \
	mv .env.tmp .env; chmod 600 .env; \
	echo "New admin key written to .env (not shown). Run 'make real-up': the old key stops working then."

# ---------------- the real stack: Ollama juniors, Claude senior, MLServer policy ----------------
REAL = docker compose -f docker-compose.yml -f infra/compose.real.yml --profile policy

real-up: env     ## start/restart router + policy server with the exported champion (Ollama must run)
	$(DOCKER_UP)
	$(OLLAMA_UP)
	$(REAL) up -d --wait --build --force-recreate postgres policy-server router

real-ask:        ## a few real requests through the stack (< $0.01); prints the routing records
	$(PY) smoke/ask.py

monitoring-up:   ## Prometheus (localhost:9090) + Grafana dashboard (localhost:3000), provisioned from deploy/
	$(DOCKER_UP)
	$(REAL) --profile monitoring up -d --wait prometheus grafana
	@echo "Dashboard: http://localhost:3000  (alerts: http://localhost:9090/alerts)"

demo:            ## open the demo page (after real-up); type your caller API key into it
	@echo "Demo page: http://localhost:8000/demo"
	@open http://localhost:8000/demo 2>/dev/null || true

demo-samples:    ## rebuild the 40 labelled test-split questions the demo page replays
	$(PY) -m pipelines.make_demo_samples

real-down:       ## stop the real stack (data volumes are kept)
	$(REAL) --profile monitoring down

# ---------------- data operations (feedback A5) and database integration tests ----------------
test-integration: ## integration tests against a throwaway PostgreSQL 16 container (127.0.0.1:55432)
	$(DOCKER_UP)
	docker run -d --rm --name router-it-pg -e POSTGRES_PASSWORD=test -p 127.0.0.1:55432:5432 postgres:16-alpine >/dev/null
	@until docker exec router-it-pg pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
	TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/postgres $(PY) -m pytest -m integration -p no:cacheprovider; \
		status=$$?; docker stop router-it-pg >/dev/null; exit $$status

backup:          ## pg_dump of the router database to backups/ (git-ignored)
	$(DOCKER_UP)
	@mkdir -p backups
	docker compose exec -T postgres pg_dump -U postgres -d router -Fc > backups/router-$$(date +%Y%m%d-%H%M).dump
	@ls -lh backups | tail -1

restore-test:    ## timed backup + restore into a scratch database, verified by row counts
	$(DOCKER_UP)
	$(PY) smoke/backup_restore.py

retention:       ## apply the retention rules now (DRY=1 to preview); normally the daily Airflow DAG
	$(DOCKER_UP)
	$(LIFECYCLE) up -d --wait airflow
	$(LIFECYCLE) exec -T -w /opt/project airflow /opt/pipelines-venv/bin/python -m pipelines.retention --text-days 30 --row-days 180 $(if $(DRY),--dry-run,)

# ---------------- held-out evaluation on the test split (never used for training or the gate) ----------------
eval-seniors:    ## Opus 5.5 + Haiku 5.5 on the 689 test questions (~10 min, ~$2; resumable, stops at $3 each)
	$(PY) -m pipelines.evaluate seniors --model claude-opus-5-5 --tag opus --effort low
	$(PY) -m pipelines.evaluate seniors --model claude-haiku-5-5 --tag haiku --effort low --thinking disabled

eval-report:     ## evaluation table + cost/accuracy sweep; policy probabilities from MLServer (make real-up), saved to results/ for --policy-probs re-runs
	$(DOCKER_UP)
	$(PY) -m pipelines.evaluate report

# ---------------- senior (Claude Opus 5.5) and Experiment 4 (architecture feedback A4) ----------------
senior-smoke:    ## one real call to Claude Opus 5.5 (< $0.01); RECORD=1 saves a scrubbed test fixture
	$(PY) exp4_senior_baseline/run_e4.py smoke $(if $(RECORD),--record,)

e4:              ## E4: Opus 5.5 and Haiku 5.5 on the 200 E1 questions; each run stops at $2 of actual spend
	$(PY) exp4_senior_baseline/run_e4.py run --model claude-opus-5-5 --tag opus --effort low
	$(PY) exp4_senior_baseline/run_e4.py run --model claude-haiku-5-5 --tag haiku --effort low --thinking disabled
	$(PY) exp4_senior_baseline/run_e4.py summarize

# ---------------- real replay pool: training and evaluation data for the policy ----------------
replay-prepare:  ## sample 3,500 MMLU + 3,500 Banking77 questions (fixed seed, checksummed)
	$(PY) -m pipelines.build_replay prepare

replay-juniors:  ## both juniors answer every question (Ollama running); resumable; keeps the Mac awake
	caffeinate -i $(PY) -m pipelines.build_replay run

replay-assemble: ## join questions and answers into data/replay/pool_v1.jsonl (+ SHA-256)
	$(PY) -m pipelines.build_replay assemble

# ---------------- Experiment 3 (architecture feedback A3) ----------------
E3_TS := $(shell date +%Y%m%d-%H%M)
E3_IN_CONTAINER = docker compose run --rm --no-deps -T \
	-v "$(CURDIR)/exp3_container_ollama:/e3:ro" --entrypoint python router /e3/run_e3.py

e3: env          ## E3: host -> Ollama vs router container -> Ollama (Ollama + Docker Desktop running)
	$(OLLAMA_UP)
	$(DOCKER_UP)
	$(PY) exp3_container_ollama/run_e3.py measure --url http://localhost:11434 --label host_a --out results/e3_raw_host_a_$(E3_TS).json
	$(E3_IN_CONTAINER) measure --url http://host.docker.internal:11434 --label container > results/e3_raw_container_$(E3_TS).json || true
	$(PY) exp3_container_ollama/run_e3.py measure --url http://localhost:11434 --label host_b --out results/e3_raw_host_b_$(E3_TS).json
	$(PY) exp3_container_ollama/run_e3.py summarize results/e3_raw_host_a_$(E3_TS).json results/e3_raw_container_$(E3_TS).json results/e3_raw_host_b_$(E3_TS).json --out results/e3_summary_$(E3_TS).md

# ---------------- feasibility checks (Docker Desktop must be running) ----------------
COMPOSE = docker compose --profile mocks

env:             ## create .env from the example on first use (local testing values)
	@[ -f .env ] || (cp .env.example .env && echo "created .env from .env.example")

s1: env          ## S1: core stack starts, serves all modes, fails safely, recovers
	$(PY) smoke/s1_compose.py

e2e: env         ## end-to-end tests on the Compose stack with MOCK models (no Ollama, no API cost)
	$(DOCKER_UP)
	AUDIT_RATE=0 BREAKER_COOLDOWN_S=0 $(COMPOSE) up -d --wait --build postgres mock-models router
	E2E_ROUTER_URL=http://localhost:8000 $(PY) -m pytest -m e2e -p no:cacheprovider; status=$$?; \
		$(COMPOSE) up -d --wait router; exit $$status

p1: env          ## P1: load test on the MOCK stack: router overhead, success, throughput (no API cost)
	$(DOCKER_UP)
	AUDIT_RATE=0 $(COMPOSE) up -d --wait --build postgres mock-models router
	$(PY) smoke/p1_load.py; status=$$?; $(COMPOSE) up -d --wait router; exit $$status

category-report: ## FR-17: per-category quality report (release split) and its effect on the test split
	$(PY) -m pipelines.category_report

s3: env          ## S3: canary test, sensitive data never leaves (breaker cooldown 0, every answer audited)
	$(DOCKER_UP)
	BREAKER_COOLDOWN_S=0 AUDIT_RATE=1 $(COMPOSE) up -d --wait --build router
	$(PY) smoke/s3_canary.py; status=$$?; $(COMPOSE) up -d --wait router; exit $$status

s2: env          ## S2: Airflow runs the pipeline, MLflow registers the challenger (big download first time)
	$(PY) smoke/s2_airflow_mlflow.py

down:            ## stop every service in every profile (data volumes are kept)
	docker compose --profile mocks --profile policy --profile monitoring --profile orchestration --profile lifecycle down
