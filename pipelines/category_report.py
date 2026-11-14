"""FR-17: per-category quality report on the release-evaluation split.

For a help-desk message the category the router can see is the request type both local models
agreed on, so a category's false agreement is the share of its agreed answers that are wrong.
Categories that fail too often (observed false agreement above the NFR-10 bar of 25%, with at
least MIN_AGREED agreed answers) are listed as "always escalate" candidates. The observed rate,
not the upper bound, decides: with 8-15 answers the upper bound is above 25% even for a category
with no errors at all, which is too little evidence, not a failing category. The list is chosen on the release split and its effect
is measured on the held-out test split, with Opus's recorded answers for the escalated ones,
next to the agreement rule (rule-v0) computed the same way.

Exam questions (task type "choice") have no category visible at request time, so their subjects
are reported for information only and never enter the list.

    python -m pipelines.category_report          # writes results/category_report_<date>.md/.json
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.metrics import wilson  # noqa: E402
from common.splits import split_of  # noqa: E402

POOL = ROOT / "data" / "replay" / "pool_v1.jsonl"
OPUS = ROOT / "results" / "eval_senior_opus.jsonl"
RESULTS = ROOT / "results"
BAR = 0.25  # NFR-10: upper 95% bound of false agreement
MIN_AGREED = 8  # fewer agreed answers than this: too little evidence to judge a category


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def category(row: dict) -> str | None:
    """The category visible at request time: the agreed request type of a help-desk message."""
    if row["task_type"] != "label" or not row["agreed"]:
        return None
    return row["j1_answer"]


def category_stats(rows: list[dict]) -> dict[str, dict]:
    """Agreed answers and wrong agreed answers per category, with the Wilson upper bound."""
    agreed: dict[str, int] = defaultdict(int)
    wrong: dict[str, int] = defaultdict(int)
    for r in rows:
        c = category(r)
        if c is None:
            continue
        agreed[c] += 1
        wrong[c] += int(r["j1_answer"] != r["gold"])
    return {
        c: {
            "agreed": agreed[c],
            "wrong": wrong[c],
            "fa": wrong[c] / agreed[c],
            "fa_upper95": wilson(wrong[c], agreed[c])[1],
        }
        for c in agreed
    }


def flagged(stats: dict[str, dict], bar: float = BAR, min_agreed: int = MIN_AGREED) -> list[str]:
    """Categories to escalate always: enough agreed answers and an observed rate above the bar."""
    return sorted(c for c, s in stats.items() if s["agreed"] >= min_agreed and s["fa"] > bar)


def subject_stats(rows: list[dict]) -> dict[str, dict]:
    """Exam questions per subject (information only: not visible to the router)."""
    out: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in rows:
        if r["task_type"] == "choice" and r["agreed"]:
            out[r["subject"]][0] += 1
            out[r["subject"]][1] += int(r["j1_answer"] != r["gold"])
    return {s: {"agreed": a, "wrong": w, "fa": w / a} for s, (a, w) in out.items()}


def simulate(rows: list[dict], opus: dict[str, dict], always_escalate: set[str]) -> dict:
    """Accept agreed answers outside the list; send the rest to Opus (recorded answers)."""
    correct = escalated = 0
    cost = 0.0
    for r in rows:
        if r["agreed"] and category(r) not in always_escalate:
            correct += int(r["j1_answer"] == r["gold"])
            continue
        escalated += 1
        o = opus.get(r["question_id"], {})
        cost += o.get("cost_usd") or 0.0
        correct += int(bool(o.get("ok")) and o.get("answer") == r["gold"])
    n = len(rows)
    lo, hi = wilson(correct, n)
    return {
        "n": n,
        "accuracy": correct / n,
        "accuracy_ci95": [lo, hi],
        "escalation": escalated / n,
        "cost_per_1000": 1000 * cost / n,
    }


def render(stats, flags, subjects, base, with_list, n_release, n_test) -> str:
    judged = [c for c, s in stats.items() if s["agreed"] >= MIN_AGREED]
    lines = [
        "# FR-17 per-category quality report",
        "",
        f"Generated {date.today().isoformat()} by `pipelines/category_report.py` from "
        f"`data/replay/pool_v1.jsonl` (junior timeout applied). Categories chosen on the "
        f"release-evaluation split ({n_release} questions); effect measured on the test split "
        f"({n_test} questions) with Opus's recorded answers (`results/eval_senior_opus.jsonl`).",
        "",
        f"A help-desk category is the request type both local models agreed on. {len(stats)} "
        f"categories had agreed answers; {len(judged)} had at least {MIN_AGREED} and could be "
        f"judged; **{len(flags)} fail too often** (observed false agreement above the NFR-10 bar of "
        f"{BAR:.0%}).",
        "",
        "## Always-escalate candidates (release split)",
        "",
        "| Category | Agreed | Wrong | False agreement | Upper 95% |",
        "|---|---|---|---|---|",
    ]
    for c in sorted(flags, key=lambda c: -stats[c]["fa"]):
        s = stats[c]
        lines.append(
            f"| {c} | {s['agreed']} | {s['wrong']} | {s['fa']:.0%} | {s['fa_upper95']:.0%} |"
        )
    lines += [
        "",
        "## Effect on the test split",
        "",
        "| Strategy | Accuracy (95% CI) | Escalated | Paid cost / 1,000 |",
        "|---|---|---|---|",
    ]
    for name, m in (("Rule-v0 (agreement)", base), ("Rule-v0 + always-escalate list", with_list)):
        lo, hi = m["accuracy_ci95"]
        lines.append(
            f"| {name} | {m['accuracy']:.1%} ({lo:.1%}–{hi:.1%}) | {m['escalation']:.0%} | "
            f"${m['cost_per_1000']:.2f} |"
        )
    worst = sorted(subjects.items(), key=lambda kv: -kv[1]["fa"])[:8]
    lines += [
        "",
        "## Exam subjects (information only, not visible to the router)",
        "",
        "| Subject | Agreed | Wrong | False agreement |",
        "|---|---|---|---|",
    ]
    lines += [f"| {s} | {v['agreed']} | {v['wrong']} | {v['fa']:.0%} |" for s, v in worst]
    return "\n".join(lines) + "\n"


def main() -> None:
    rows = read_jsonl(POOL)
    release = [r for r in rows if split_of(r["group"]) == "release_eval"]
    test = [r for r in rows if split_of(r["group"]) == "test"]
    opus = {o["question_id"]: o for o in read_jsonl(OPUS)}
    stats = category_stats(release)
    flags = flagged(stats)
    subjects = subject_stats(release)
    base = simulate(test, opus, set())
    with_list = simulate(test, opus, set(flags))
    stamp = date.today().isoformat()
    md = render(stats, flags, subjects, base, with_list, len(release), len(test))
    (RESULTS / f"category_report_{stamp}.md").write_text(md, encoding="utf-8")
    payload = {
        "bar": BAR,
        "min_agreed": MIN_AGREED,
        "always_escalate": flags,
        "categories": stats,
        "test_effect": {"rule_v0": base, "rule_v0_with_list": with_list},
    }
    (RESULTS / f"category_report_{stamp}.json").write_text(
        json.dumps(payload, indent=1), encoding="utf-8"
    )
    print(md)


if __name__ == "__main__":
    main()
