"""Build the fixed, checksummed question set for Experiment 1.

choice: MMLU test split (cais/mmlu, config "all"), 4 options, gold letter.
label:  Banking77 test split (mteb/banking77, parquet copy with label_text), customer-support
        messages, gold = one of 77 category names. Stands in for the help-desk scenario.
        (PolyAI/banking77 is not used: it needs a loading script that `datasets` no longer runs.)

Usage:  python exp1_juniors/prepare_data.py --n-choice 100 --n-label 100
Output: data/exp1_questions.jsonl, data/banking77_labels.json (+ SHA-256 of the question file)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "exp1_questions.jsonl"
LABELS_OUT = ROOT / "data" / "banking77_labels.json"
SEED = 42


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-choice", type=int, default=100)
    ap.add_argument("--n-label", type=int, default=100)
    args = ap.parse_args()

    from datasets import load_dataset  # imported here so --help works without it

    rng = random.Random(SEED)
    rows: list[dict] = []

    mmlu = load_dataset("cais/mmlu", "all", split="test")
    for i in rng.sample(range(len(mmlu)), args.n_choice):
        r = mmlu[i]
        options = "\n".join(f"{l}. {c}" for l, c in zip("ABCD", r["choices"]))
        rows.append({
            "id": f"mmlu-{i}",
            "task_type": "choice",
            "subject": r["subject"],
            "question": r["question"],
            "options": options,
            "gold": "ABCD"[r["answer"]],
        })

    b77 = load_dataset("mteb/banking77", split="test")
    labels = sorted(set(b77["label_text"]))
    assert len(labels) == 77, f"expected 77 categories, got {len(labels)}"
    for i in rng.sample(range(len(b77)), args.n_label):
        r = b77[i]
        rows.append({
            "id": f"banking77-{i}",
            "task_type": "label",
            "subject": "banking77",
            "question": r["text"],
            "options": "",          # the category list is shared; it lives in banking77_labels.json
            "gold": r["label_text"],
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    LABELS_OUT.write_text(json.dumps(labels, indent=1) + "\n", encoding="utf-8")
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    OUT.write_text(payload, encoding="utf-8")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    (OUT.with_suffix(".sha256")).write_text(f"{digest}  {OUT.name}\n")
    print(f"wrote {len(rows)} questions to {OUT} ({args.n_choice} choice, {args.n_label} label)")
    print(f"wrote {len(labels)} categories to {LABELS_OUT}\nsha256 {digest}")


if __name__ == "__main__":
    main()
