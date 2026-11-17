# Lifecycle demo (2026-11-17T21:36:48+04:00)

**Result: FAIL.** Train, gate, shadow report, promote and roll back on the running stack; the router and MLServer switch models without a restart (I7).

| Step | What happened | Check |
|---|---|---|
| 1 before | serving run-e1d8cf42; router container started 2026-11-17T15:31:12.633367384Z | INFO |
| 1 before | request answered (juniors_agreed, $0.0000); decided by rule-v0-fallback | FAIL |
| 2 gate | promoting version 2 (policy-features-v1, failed the gate) was refused; still serving run-e1d8cf42 | PASS |
| 3 train | DAG run in 8 s: challenger = version 8 (run-81bcb9e0, gate passed); shadow report: eligible for promotion | PASS |
| 4 promote | `promote --version 8` took 3.0 s; now serving run-e1d8cf42; router restarted: no | FAIL |
| 4 promote | 13 requests during the switch, 0 failed (decided by: rule-v0-fallback x13) | PASS |
| 4 promote | request answered (juniors_agreed, $0.0000); decided by rule-v0-fallback | FAIL |
| 5 rollback | `rollback` took 3.0 s; now serving run-e1d8cf42; router restarted: no | FAIL |
| 5 rollback | 13 requests during the switch, 0 failed (decided by: rule-v0-fallback x13) | PASS |
| 5 rollback | request answered (juniors_agreed, $0.0000); decided by rule-v0-fallback | FAIL |
