# Lifecycle demo (2026-11-12T19:20:32+04:00)

**Result: PASS.** Train, gate, shadow report, promote and roll back on the running stack; the router and MLServer switch models without a restart (I7).

| Step | What happened | Check |
|---|---|---|
| 1 before | serving run-e1d8cf42; router container started 2026-11-12T15:19:55.430315841Z | INFO |
| 1 before | request answered (policy_accepted, $0.0000); decided by run-e1d8cf42 | PASS |
| 2 gate | promoting version 2 (policy-features-v1, failed the gate) was refused; still serving run-e1d8cf42 | PASS |
| 3 train | DAG run in 9 s: challenger = version 4 (run-ec2df3f2, gate passed); shadow report: eligible for promotion | PASS |
| 4 promote | `promote --version 4` took 2.9 s; now serving run-ec2df3f2; router restarted: no | PASS |
| 4 promote | 13 requests during the switch, 0 failed (decided by: run-e1d8cf42 x7, run-ec2df3f2 x6) | PASS |
| 4 promote | request answered (policy_accepted, $0.0000); decided by run-ec2df3f2 | PASS |
| 5 rollback | `rollback` took 2.9 s; now serving run-e1d8cf42; router restarted: no | PASS |
| 5 rollback | 13 requests during the switch, 0 failed (decided by: run-ec2df3f2 x7, run-e1d8cf42 x6) | PASS |
| 5 rollback | request answered (policy_accepted, $0.0000); decided by run-e1d8cf42 | PASS |
