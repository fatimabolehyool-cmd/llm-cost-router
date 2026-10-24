# Experiment 1a, rescored with the fixed normalizer (run 20261022-1953)

All figures measured on this machine; none are estimates.

```
{
  "rescored_from": "exp1_screen_raw_20261022-1953.jsonl",
  "answers_changed_by_new_normalizer": 2,
  "normalizer": "option-aware (reads 'C. both A and B')",
  "prompt_version": "junior-prompts-v2",
  "dataset_sha256": "d14502cddc15c0dd6917c739c9f2b8a9c253a60b8a7d83f1886c0c20c2560da5",
  "note": "latencies are the original measurements from the raw file"
}
```

## Per model

| Model | Accuracy choice | Accuracy label | Unreadable | Errors | p50 ms (choice) | p95 ms (choice) | p50 ms (label) | p95 ms (label) |
|---|---|---|---|---|---|---|---|---|
| gemma3:4b | 50.0% | 58.0% | 1 | 0 | 103 | 183 | 270 | 327 |
| qwen3:4b | 7.0% | 0.0% | 180 | 0 | 419 | 514 | 417 | 429 |
| phi4-mini | 54.0% | 50.0% | 10 | 0 | 132 | 386 | 84 | 131 |
| llama3.2:3b | 49.0% | 47.0% | 4 | 0 | 56 | 132 | 72 | 100 |

## Every pair (agreement signal)

Agree = both answers readable and equal (the request is NOT escalated). 'Acc if agree' = accuracy of the agreed answer. 'Acc if disagree' = accuracy of junior 1 when they disagree. Gap = the difference (target >= 20 points). False agreement = wrong although they agreed (target <= 25%); its upper 95% bound is what the release gate checks. 'Same wrong answer' = share of ALL questions where both juniors gave the same wrong answer (correlated error, ADR-7).

| Pair | Agree | Acc if agree | Acc if disagree | Gap (pts) | False agreement | FA upper 95% | Same wrong answer | Choice agree acc | Label agree acc |
|---|---|---|---|---|---|---|---|---|---|
| gemma3:4b + qwen3:4b | 4% | 50.0% | 54.2% | -4.2 | 50.0% | 78.5% | 2.0% | 57% (n=7) | 0% (n=1) |
| gemma3:4b + phi4-mini | 61% | 67.2% | 33.3% | 33.9 | 32.8% | 41.5% | 20.0% | 61% (n=64) | 74% (n=58) |
| gemma3:4b + llama3.2:3b | 52% | 68.3% | 38.5% | 29.7 | 31.7% | 41.2% | 16.5% | 61% (n=56) | 77% (n=48) |
| qwen3:4b + phi4-mini | 4% | 50.0% | 1.6% | 48.4 | 50.0% | 78.5% | 2.0% | 50% (n=8) | - |
| qwen3:4b + llama3.2:3b | 4% | 42.9% | 2.1% | 40.8 | 57.1% | 84.2% | 2.0% | 43% (n=7) | - |
| phi4-mini + llama3.2:3b | 56% | 64.6% | 35.6% | 29.0 | 35.4% | 44.6% | 20.0% | 60% (n=65) | 71% (n=48) |

Best eligible pair: **gemma3:4b + llama3.2:3b** (lowest false-agreement upper bound 41.2%, false agreement 31.7%, gap 29.7 points). Eligible = agree on >= 30% of questions and each model unreadable on <= 10%. Check latency in the per-model table too.

n = 200 questions; a percentage is only good to roughly ±7 points (95%). Treat small differences as ties.
