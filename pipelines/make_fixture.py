"""Create the SYNTHETIC replay fixture used by feasibility check S2 and the lifecycle demo.

It has the same schema as real replay data (one row per question with both junior answers and
whether junior 1 was correct), but the values are generated. It proves the pipeline mechanics,
NOT the policy's quality. Real replay data comes from Experiment 1 and RouterBench later.

Usage: python -m pipelines.make_fixture   ->  data/replay/fixture_v1.jsonl (+ .sha256)
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "replay" / "fixture_v1.jsonl"


def main(n: int = 3000, seed: int = 7) -> None:
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        task = "choice" if rng.random() < 0.5 else "label"
        k = 4 if task == "choice" else 77
        j1_ok, j2_ok = rng.random() < 0.97, rng.random() < 0.97
        agreed = j1_ok and j2_ok and rng.random() < (0.62 if task == "choice" else 0.55)
        p_correct = (0.80 if agreed else 0.45) - (0.06 if task == "label" else 0.0)
        p_correct -= 0.0004 * max(0, rng.randint(40, 700) - 300)  # longer prompts slightly harder
        rows.append(
            {
                "question_id": f"synth-{i:05d}",
                "group": f"g{i // 3:04d}",  # near-duplicates share a group (split by group)
                "task_type": task,
                "n_options": k,
                "agreed": agreed,
                "j1_ok": j1_ok,
                "j2_ok": j2_ok,
                "prompt_tokens": rng.randint(40, 700),
                "j1_tokens": rng.randint(1, 6),
                "j2_tokens": rng.randint(1, 6),
                "junior_correct": int(rng.random() < max(0.05, p_correct)),
                "label_source": "synthetic",
            }
        )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(r) + "\n" for r in rows)
    OUT.write_text(payload)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    OUT.with_suffix(".sha256").write_text(f"{digest}  {OUT.name}\n")
    print(f"wrote {n} rows to {OUT}\nsha256 {digest}")


if __name__ == "__main__":
    main()
