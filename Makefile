PY ?= .venv/bin/python
MODELS ?= gemma3:4b qwen3:4b phi4-mini llama3.2:3b

.PHONY: venv test pull data exp1-screen exp1-load exp2-build exp2-train exp2-up exp2-bench exp2-stats exp2-down

venv:            ## create the host virtual environment
	python3 -m venv .venv && .venv/bin/pip install -q -r requirements-host.txt
	mkdir -p results && .venv/bin/pip freeze > results/host-env.txt

test:            ## unit tests for the answer normalizer
	$(PY) -m pytest -q tests

pull:            ## download the candidate junior models into Ollama
	for m in $(MODELS); do ollama pull $$m; done

data:            ## 100 choice + 100 label questions, fixed seed, checksummed
	$(PY) exp1_juniors/prepare_data.py

exp1-screen:     ## Experiment 1a: every model on every question, every pair scored
	$(PY) exp1_juniors/run_exp1.py screen --models $(MODELS)

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
