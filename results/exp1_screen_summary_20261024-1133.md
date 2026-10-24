# Experiment 1a: junior screening (20261024-1133)

All figures measured on this machine; none are estimates.

```
{
  "timestamp_utc": "2026-10-24T07:33:05+00:00",
  "machine": "arm64",
  "macos": "27.0.1",
  "python": "3.14.7",
  "ollama_version": "ollama version is 0.40.8",
  "ollama_ps": "NAME           ID              SIZE      PROCESSOR    CONTEXT    RUNNER      UNTIL               \nphi4:14b       ac896e5b8b34    9.3 GB    100% GPU     4096       llamacpp    29 minutes from now    \nqwen2.5:7b     845dbda0ea48    4.8 GB    100% GPU     4096       llamacpp    28 minutes from now    \nllama3.1:8b    46e0c10c039e    5.3 GB    100% GPU     4096       llamacpp    27 minutes from now",
  "prompt_version": "junior-prompts-v2",
  "dataset_sha256": "d14502cddc15c0dd6917c739c9f2b8a9c253a60b8a7d83f1886c0c20c2560da5"
}
```

## Per model

| Model | Accuracy choice | Accuracy label | Unreadable | Errors | p50 ms (choice) | p95 ms (choice) | p50 ms (label) | p95 ms (label) |
|---|---|---|---|---|---|---|---|---|
| gemma3:12b | 64.0% | 68.0% | 3 | 0 | 238 | 545 | 783 | 926 |
| llama3.1:8b | 58.0% | 45.0% | 2 | 0 | 115 | 299 | 143 | 198 |
| qwen2.5:7b | 66.0% | 51.0% | 4 | 0 | 110 | 281 | 143 | 202 |
| phi4:14b | 74.0% | 55.0% | 13 | 0 | 407 | 1340 | 328 | 1157 |

## Every pair (agreement signal)

Agree = both answers readable and equal (the request is NOT escalated). 'Acc if agree' = accuracy of the agreed answer. 'Acc if disagree' = accuracy of junior 1 when they disagree. Gap = the difference (target >= 20 points). False agreement = wrong although they agreed (target <= 25%); its upper 95% bound is what the release gate checks. 'Same wrong answer' = share of ALL questions where both juniors gave the same wrong answer (correlated error, ADR-7).

| Pair | Agree | Acc if agree | Acc if disagree | Gap (pts) | False agreement | FA upper 95% | Same wrong answer | Choice agree acc | Label agree acc |
|---|---|---|---|---|---|---|---|---|---|
| gemma3:12b + llama3.1:8b | 62% | 71.0% | 57.9% | 13.1 | 29.0% | 37.6% | 18.0% | 72% (n=64) | 70% (n=60) |
| gemma3:12b + qwen2.5:7b | 68% | 75.2% | 46.0% | 29.2 | 24.8% | 32.7% | 17.0% | 73% (n=78) | 78% (n=59) |
| gemma3:12b + phi4:14b | 72% | 78.3% | 35.1% | 43.2 | 21.7% | 29.1% | 15.5% | 77% (n=75) | 79% (n=68) |
| llama3.1:8b + qwen2.5:7b | 62% | 68.0% | 24.0% | 44.0 | 32.0% | 40.6% | 20.0% | 70% (n=73) | 65% (n=52) |
| llama3.1:8b + phi4:14b | 60% | 74.2% | 17.5% | 56.7 | 25.8% | 34.3% | 15.5% | 77% (n=66) | 70% (n=54) |
| qwen2.5:7b + phi4:14b | 65% | 76.9% | 24.3% | 52.6 | 23.1% | 31.0% | 15.0% | 80% (n=74) | 73% (n=56) |

Best eligible pair: **gemma3:12b + phi4:14b** (lowest false-agreement upper bound 29.1%, false agreement 21.7%, gap 43.2 points). Eligible = agree on >= 30% of questions and each model unreadable on <= 10%. Check latency in the per-model table too.

n = 200 questions; a percentage is only good to roughly ±7 points (95%). Treat small differences as ties.
