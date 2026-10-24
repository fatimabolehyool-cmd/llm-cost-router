# Experiment 1b: parallel juniors under load (20261024-1208)

Pair: gemma3:12b + phi4:14b. Each request = both juniors called in parallel (router behaviour). Timeout per call 120 s. Same 24 questions at every level.

```
{
  "timestamp_utc": "2026-10-24T08:07:15+00:00",
  "machine": "arm64",
  "macos": "27.0.1",
  "python": "3.14.7",
  "ollama_version": "ollama version is 0.40.8",
  "ollama_ps": "NAME          ID              SIZE      PROCESSOR    CONTEXT    RUNNER      UNTIL               \nphi4:14b      ac896e5b8b34    12 GB     100% GPU     4096       llamacpp    29 minutes from now    \ngemma3:12b    b92b218582c5    9.6 GB    100% GPU     4096       llamacpp    29 minutes from now",
  "prompt_version": "junior-prompts-v2",
  "dataset_sha256": "d14502cddc15c0dd6917c739c9f2b8a9c253a60b8a7d83f1886c0c20c2560da5"
}
```

| Concurrent requests | Requests | Success | Stage p50 ms | Stage p95 ms | Requests/s | Timeouts |
|---|---|---|---|---|---|---|
| 1 | 24 | 100% | 1179 | 2003 | 0.89 | 0 |
| 4 | 24 | 100% | 2711 | 5988 | 1.31 | 0 |
| 8 | 24 | 100% | 4456 | 6638 | 1.80 | 0 |
