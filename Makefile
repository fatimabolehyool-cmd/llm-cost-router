PY ?= .venv/bin/python
MODELS ?= gemma3:4b qwen3:4b phi4-mini llama3.2:3b
# Mid-size screen (24 Oct): non-thinking instruct models from 4 labs, ~27 GB download.
# qwen3 was dropped: it reasons out loud and never reached an answer within the token limit.
MODELS_MID ?= gemma3:12b llama3.1:8b qwen2.5:7b phi4:14b

.PHONY: venv test lint format cov audit replay-prepare replay-juniors replay-assemble e3 pull data exp1-screen exp1-rescore pull-mid exp1-screen-mid exp1-load exp2-build exp2-train exp2-up exp2-bench exp2-stats exp2-down env s1 s2 s3 down

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

s3: env          ## S3: canary test, sensitive data never leaves (breaker cooldown 0 during the test)
	BREAKER_COOLDOWN_S=0 $(COMPOSE) up -d --wait --build router
	$(PY) smoke/s3_canary.py; status=$$?; $(COMPOSE) up -d --wait router; exit $$status

s2: env          ## S2: Airflow runs the pipeline, MLflow registers the challenger (big download first time)
	$(PY) smoke/s2_airflow_mlflow.py

down:            ## stop everything (data volumes are kept)
	docker compose --profile mocks --profile orchestration down
