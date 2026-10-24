# Experiment 1a: junior screening (20261022-1953)

All figures measured on this machine; none are estimates.

```
{
  "timestamp_utc": "2026-10-22T15:53:30+00:00",
  "machine": "arm64",
  "macos": "27.0.1",
  "python": "3.14.7",
  "ollama_version": "ollama version is 0.40.8",
  "ollama_ps": "NAME           ID              SIZE      PROCESSOR    CONTEXT    RUNNER      UNTIL               \nllama3.2:3b    a80c4f17acd5    2.6 GB    100% GPU     4096       llamacpp    29 minutes from now    \nqwen3:4b       359d7dd4bcda    3.2 GB    100% GPU     4096       llamacpp    29 minutes from now    \ngemma3:4b      68ceab7e468a    2.3 GB    100% GPU     4096       llamacpp    27 minutes from now",
  "prompt_version": "junior-prompts-v2",
  "dataset_sha256": "d14502cddc15c0dd6917c739c9f2b8a9c253a60b8a7d83f1886c0c20c2560da5"
}
```

## Per model

| Model | Accuracy choice | Accuracy label | Unreadable | Errors | p50 ms (choice) | p95 ms (choice) | p50 ms (label) | p95 ms (label) |
|---|---|---|---|---|---|---|---|---|
| gemma3:4b | 50.0% | 58.0% | 1 | 0 | 103 | 183 | 270 | 327 |
| qwen3:4b | 7.0% | 0.0% | 180 | 0 | 419 | 514 | 417 | 429 |
| phi4-mini | 53.0% | 50.0% | 12 | 0 | 132 | 386 | 84 | 131 |
| llama3.2:3b | 49.0% | 47.0% | 4 | 0 | 56 | 132 | 72 | 100 |

## Every pair (agreement signal)

Agree = both answers readable and equal. 'Acc if agree' = accuracy of the agreed answer. 'Acc if disagree' = accuracy of junior 1 when they disagree. Gap = the difference (proposal target: at least 20 points). False agreement = wrong although they agreed (proposal target: at most 25%).

| Pair | Agree (= not escalated) | Acc if agree | Acc if disagree | Gap (pts) | False agreement | Choice agree acc | Label agree acc |
|---|---|---|---|---|---|---|---|
| gemma3:4b + qwen3:4b | 4% | 50.0% | 54.2% | -4.2 | 50.0% | 57% (n=7) | 0% (n=1) |
| gemma3:4b + phi4-mini | 60% | 67.5% | 33.8% | 33.8 | 32.5% | 61% (n=62) | 74% (n=58) |
| gemma3:4b + llama3.2:3b | 52% | 68.3% | 38.5% | 29.7 | 31.7% | 61% (n=56) | 77% (n=48) |
| qwen3:4b + phi4-mini | 4% | 50.0% | 1.6% | 48.4 | 50.0% | 50% (n=8) | - |
| qwen3:4b + llama3.2:3b | 4% | 42.9% | 2.1% | 40.8 | 57.1% | 43% (n=7) | - |
| phi4-mini + llama3.2:3b | 56% | 64.9% | 34.8% | 30.0 | 35.1% | 60% (n=63) | 71% (n=48) |

Largest gap: **qwen3:4b + phi4-mini** (48.4 points). Choose the pair on gap AND false agreement AND latency, not gap alone.

n = 200 questions; with this n, a percentage is only good to roughly ±7 points (95%). Treat small differences as ties.
