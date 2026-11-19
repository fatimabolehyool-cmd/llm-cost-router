# Glossary

The words used in the code, the results files and the report, with one meaning each.

| Term | Meaning |
| --- | --- |
| Junior | One of the two local models that answer first: gemma3:12b and phi4:14b on Ollama |
| Senior | The premium hosted model used on escalation: Claude Opus 5.5 |
| Agree | Both juniors' replies normalize to the same readable option |
| Normalizer | The code that turns a model's free text into one allowed option or "unreadable" (`common/normalizer.py`) |
| Mode | `cheapest` (never pays; disagreement returns an unverified junior answer), `balanced` (the policy decides) or `best_quality` (always asks the senior unless sensitive) |
| Policy | The trained model that predicts P(junior answer correct); its threshold sets the balanced mode |
| rule-v0 | The agreement rule: accept the junior answer when both juniors agree. The first champion, and the fallback when the policy server is unavailable |
| Champion / challenger | The policy version serving now / a candidate being evaluated (MLflow registry aliases) |
| Release gate | The rule a challenger must pass before it may be promoted (`common/release_gate.py`) |
| Shadow report | An offline, request-by-request comparison of a challenger with the serving champion on replayed traffic (`pipelines/shadow.py`) |
| Escalate | Ask the senior because the junior answer cannot be trusted |
| Abstain | Return no answer, with a reason, because the answer cannot be trusted and escalation is not allowed |
| Data class | `sensitive` or `non_sensitive`, set by the caller's flag or the PII detector |
| Egress gateway | The only code that can call an external provider; it refuses sensitive data and reserves spend first (`router/egress.py`) |
| Spend cap | The global limit on paid calls (`SPEND_CAP_USD`), reserved atomically in PostgreSQL before each call |
| False agreement | The juniors agree and the agreed answer is wrong, as a share of agreed answers |
| False acceptance | A returned junior answer that is wrong, as a share of accepted junior answers |
| Audit sampling | Re-asking 5% of accepted, non-sensitive answers to the senior after the response, to estimate false acceptance in live use |
| Answer prior | A policy-features-v2 input: how often an answer was right in the training data, per task type |
| Replay pool | Recorded junior answers on 7,000 labelled questions, used to train and evaluate the policy (`data/replay/`) |
| Splits | Train 60%, validation 15%, release evaluation 15%, test 10%, assigned by a hash of the normalized question |
| Window | A label for one re-validation run; it does not select different data yet (ADR-22) |
| Release owner | The person who approves promoting a new policy, prompt or model |
| E1–E4, S1–S3, P1 | The measured checks: E1 junior pair, E2 policy serving, E3 container to Ollama, E4 Opus and Haiku; S1 core stack, S2 Airflow and MLflow, S3 privacy canary; P1 load test |
