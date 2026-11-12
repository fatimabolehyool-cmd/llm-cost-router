"""Build router/static/demo_samples.json: labelled questions for the demo page's replay button.

40 questions from the TEST split of replay pool v1 (never used for training, thresholds or the
gate): 20 MMLU questions from a few general subjects and 20 Banking77 help-desk tickets, chosen
with a fixed seed. The Banking77 label list is stored once. Sources: MMLU (MIT licence) and
Banking77 (CC BY 4.0).

python -m pipelines.make_demo_samples
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common.splits import split_of  # noqa: E402

SUBJECTS = {
    "astronomy",
    "conceptual_physics",
    "elementary_mathematics",
    "high_school_biology",
    "high_school_computer_science",
    "high_school_geography",
    "high_school_macroeconomics",
    "marketing",
    "miscellaneous",
    "nutrition",
    "prehistory",
}
PER_TASK, SEED = 20, 7


def main() -> int:
    rows = [json.loads(x) for x in (ROOT / "data/replay/pool_v1_questions.jsonl").open()]
    test = [r for r in rows if split_of(r["group"]) == "test"]
    choice = sorted(
        (r for r in test if r["task_type"] == "choice" and r["subject"] in SUBJECTS),
        key=lambda r: r["question_id"],
    )
    label = sorted((r for r in test if r["task_type"] == "label"), key=lambda r: r["question_id"])
    rng = random.Random(SEED)
    picked = rng.sample(choice, PER_TASK) + rng.sample(label, PER_TASK)
    rng.shuffle(picked)
    out = {
        "source": "replay pool v1, test split (MMLU: MIT licence; Banking77: CC BY 4.0)",
        "labels": json.loads((ROOT / "data/banking77_labels.json").read_text()),
        "items": [
            {
                "id": r["question_id"],
                "task_type": r["task_type"],
                "question": r["question"],
                **({"options": r["options"]} if r["task_type"] == "choice" else {}),
                "gold": r["gold"],
            }
            for r in picked
        ],
    }
    path = ROOT / "router/static/demo_samples.json"
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {len(out['items'])} questions to {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
