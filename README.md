# Architecture validation experiments — Cost-Aware LLM Router

Two small experiments for Section I of the architecture deliverable. Each one tests a
risky assumption from the proposal. **Run them on the MacBook (M5 Pro, 48 GB)**: the numbers
must come from the machine the system will run on, and must be measured, not estimated.

| | Question | Proposal assumption it tests |
|---|---|---|
| **Exp 1** | Which two local models give a usable agreement signal, and how fast are they in parallel and under load? | Juniors run locally with Ollama; agreement gap ≥ 20 pts; false agreement ≤ 25% |
| **Exp 2** | Is the HTTP hop from the router to the policy server within budget? | Policy call ≤ 60 ms at p95 (ADR on the network hop) |

## One-time setup (≈ 30 min, mostly downloads)

1. Install **Docker Desktop** (Apple Silicon) and start it.
2. Install **Ollama natively**, not in Docker (Docker on macOS cannot use the Apple GPU):
   `brew install ollama` then `ollama serve` in its own terminal (or open the Ollama app).
3. In this folder:
   ```bash
   make venv        # host Python env + records exact versions in results/host-env.txt
   make test        # 25 unit tests for the answer normalizer must pass
   make pull        # ~11 GB: gemma3:4b, qwen3:4b, phi4-mini, llama3.2:3b (4 different labs)
   make data        # 100 MMLU (choice) + 100 Banking77 (label) questions, fixed seed, SHA-256 saved
   ```

## Experiment 1 — juniors (≈ 30–40 min, unattended)

```bash
make exp1-screen                              # every model × every question, every pair scored
make exp1-load PAIR="<model-a> <model-b>"     # pick the pair from the screen summary
```
Pick the pair on **gap, false agreement and latency together**, not gap alone. Close
the other apps first: background load changes latency.

## Experiment 2 — policy serving (≈ 15 min, first build downloads ~1 GB)

```bash
make exp2-build
make exp2-train      # trains a PLACEHOLDER policy on synthetic data inside the serving image
make exp2-up         # starts MLServer, prints seconds until healthy
make exp2-bench      # 500 sequential calls + 500 under stress, plus a malformed-request probe
make exp2-stats      # memory/CPU of the container
make exp2-down
```

## What to send back

Everything in `results/` (summaries `.md`, raw `.jsonl`, `host-env.txt`, `exp2_*`).
Add one line of observations, e.g. whether the fan got loud or anything failed.

## Notes and known limits

- **Scope (22 Oct):** task types are `choice` and `label` only; `number` was dropped. Label questions
  come from Banking77 (77 customer-support categories) as a stand-in for the help-desk scenario.
  It is harder than a typical 8-category help desk, so treat agreement and accuracy as conservative.

- The policy in Exp 2 is trained on **synthetic** data: the experiment measures serving, not quality.
- Already checked off the Mac (Linux, Python 3.11): normalizer tests pass; Exp 1 runs end to end
  against a mock Ollama; MLServer 1.7.1 serves the policy and the benchmark runs. The Docker
  build itself has **not** been run yet — that happens on your Mac.
- Findings so far, already useful for the ADRs: MLServer 1.7.1 does not install on Python 3.13
  (pin 3.11); MLflow 3.x refuses the folder backend (use a database); a malformed policy request
  returns HTTP 500, not 400 (the router must validate its own inputs).
