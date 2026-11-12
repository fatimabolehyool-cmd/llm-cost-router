# Interface I7: policy promotion, rollback and hot reload

Who uses it: the operator, through `make promote`, `make rollback` and `make reload` (which run
`pipelines.registry` in the Airflow container). Never callers of `/v1/answer`.

## Sequence

```
make promote VERSION=N
  └─ pipelines.registry promote --version N
       1. refuse unless version N has the tag gate_passed=True            (exit 2)
       2. MLflow aliases: previous_champion <- champion, champion <- N
       3. export N's package to policy_models/routing-policy (atomic rename)
       4. MLServer:  POST /v2/repository/models/routing-policy/load        (I5 side)
       5. router:    POST /admin/policy/reload   (X-Admin-Key)             (this interface)
```

`make rollback` is the same from step 2 with `champion <- previous_champion`. Steps 4 and 5 are
`pipelines/serving.py`; `make reload` runs only them.

**Why the order is safe.** MLServer loads the new version before it unloads the old one, so it
keeps answering (checked with MLServer 1.7.1: no failed requests while loading). Between steps 4
and 5 the router still holds the old metadata; the policy client's version check rejects replies
from the new model, so those requests are decided by rule-v0 (counted in
`router_policy_fallbacks_total`). The router never builds features for one model and sends them
to another. If step 5 fails, the old policy keeps serving and the registry command exits 3.

## Authentication

`X-Admin-Key` header. The router stores only its SHA-256 (`ROUTER_ADMIN_KEY_SHA256`); the key
itself lives in `.env` and in the Airflow container. It must differ from the caller key, and the
caller key is never accepted on admin endpoints. `make admin-key` creates or rotates it (the new
key works, and the old one stops working, after `make real-up`). Empty hash = admin disabled.

## `GET /admin/policy`

```json
200 {"policy": "run-e1d8cf42", "feature_version": "policy-features-v2", "threshold_balanced": 0.5665}
200 {"policy": "rule-v0", "feature_version": null, "threshold_balanced": null}
```

## `POST /admin/policy/reload` (no body)

Reads `policy_meta.json`, sends one probe to the policy server, and switches only if the server
answers with the same `model_version`. Requests are never paused; a request already waiting for
the policy server finishes with the policy it started with. Reloads are serialised.

```json
200 {"reloaded": true, "previous": "run-e1d8cf42", "policy": "run-c43248a9",
     "feature_version": "policy-features-v2", "threshold_balanced": 0.5665}
```

| Status | `error.code` | When | What serves afterwards |
|---|---|---|---|
| 401 | `unauthorized` | missing or wrong `X-Admin-Key` (or the caller key) | unchanged |
| 403 | `admin_disabled` | `ROUTER_ADMIN_KEY_SHA256` not set | unchanged |
| 409 | `policy_not_configured` | `POLICY_URL` not set | rule-v0 |
| 409 | `reload_failed` | metadata invalid, policy server down, or `model_version_mismatch` | the previous policy |
| 200 | (no error) | no champion exported (folder removed) | rule-v0 |

Example refusal: `{"error": {"code": "reload_failed", "message": "model_version_mismatch;
run-e1d8cf42 keeps serving"}}`.

## Evidence

- `tests/test_admin.py`: authentication, the switch reaching the next routing decision, every
  refusal above, rule-v0 with no champion.
- `tests/test_policy.py`: a request in flight keeps its policy when a reload lands mid-request.
- `tests/test_serving.py`: call order (MLServer first), failure handling, real HTTP.
- `make lifecycle-demo` → `results/lifecycle_demo_<timestamp>.md`: gate refusal, training,
  shadow report, promote and rollback under live traffic, router start time unchanged.
- Metrics: `router_policy_info{version}` (Grafana panel "Routing policy serving now") and
  `router_policy_reloads_total{result}`.
