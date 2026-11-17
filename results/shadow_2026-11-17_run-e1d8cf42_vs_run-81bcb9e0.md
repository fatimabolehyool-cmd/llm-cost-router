# Shadow comparison: run-81bcb9e0 (challenger) vs run-e1d8cf42 (champion)

Replayed **1057** requests of the `release_eval` split through both policies, using the artifacts MLServer would load and the router's feature code. Nothing was served.

| Measure | Champion | Challenger |
|---|---|---|
| Coverage (answered by the juniors) | 74.3% | 74.3% |
| Accuracy of accepted answers | 85.1% | 85.1% |
| False acceptance, upper 95% | 17.6% | 17.6% |
| Escalations | 272 | 272 |
| Senior cost per 1,000 requests | $0.69 | $0.69 |

| Decision change | Requests | Of which the junior answer was wrong |
|---|---|---|
| Same decision | 1057 | |
| Champion accepts, challenger escalates | 0 | 0 |
| Champion escalates, challenger accepts | 0 | 0 |

Where the two disagree, the challenger makes the right call 0 times and the champion 0 times (exact sign test p = 1.000; right call = accept a correct junior answer or escalate a wrong one).

Gate against the current champion: accuracy_not_worse PASS, coverage_within_2pts PASS, false_accept_upper95_le_25pct PASS, not_significantly_worse PASS

**Verdict: eligible for promotion.** Promotion stays a human decision (`make promote VERSION=...`).
Senior cost assumes $0.0027 per escalation (measured mean).
