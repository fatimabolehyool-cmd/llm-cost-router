"""Offline shadow comparison: the challenger against the policy serving now, request by request.

The release gate (pipelines/revalidate.py) compares a challenger with rule-v0. Before a human
promotes it, this report replays the release-evaluation split through BOTH the current champion
and the challenger. It uses the exact artifacts MLServer would load (model.joblib and
policy_meta.json from MLflow) and builds features the way the router does, then shows which
routing decisions would change: answers the challenger would now escalate or accept, how many of
those junior answers were wrong, the cost difference, and an exact sign test on the requests
where the two policies decide differently. Nothing is served and no response changes.

python -m pipelines.shadow --data data/replay/pool_v1.jsonl [--challenger challenger]
                           [--champion champion] [--out results]

Exit 0 = report written (whatever the verdict); 2 = no challenger to compare.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common.metrics import wilson  # noqa: E402
from common.policy_features import (  # noqa: E402
    FEATURE_VERSION,
    FEATURE_VERSION_V2,
    answer_prior,
    build,
    build_v2,
)
from common.release_gate import decide  # noqa: E402
from common.splits import split_of  # noqa: E402

MODEL = "routing-policy"
SPLIT = "release_eval"  # the gate's split; the test split is never used before the final report
SENIOR_COST_USD = 0.0027  # mean Opus 5.5 cost per call in the held-out evaluation (12 Nov)


# ---------------------------------------------------------------- policies


def vector(meta: dict, row: dict) -> list[float]:
    """Features for one replay row, built as router/policy.py PolicyMeta.vector does at serving
    time: readability and agreement come from the normalized junior answers."""
    a1, a2 = row.get("j1_answer"), row.get("j2_answer")
    args = (
        row["task_type"],
        row["n_options"],
        a1 is not None and a1 == a2,
        a1 is not None,
        a2 is not None,
        row["prompt_tokens"],
        row["j1_tokens"],
        row["j2_tokens"],
    )
    if meta["feature_version"] == FEATURE_VERSION:
        return build(*args)
    if meta["feature_version"] == FEATURE_VERSION_V2:
        return build_v2(*args, answer_prior(meta["answer_prior"], row["task_type"], a1, a2))
    raise ValueError(f"unsupported feature version {meta['feature_version']!r}")


@dataclass(frozen=True)
class Policy:
    name: str
    decide: Callable[[list[dict]], list[bool]]  # True = accept the junior answer


def rule_v0() -> Policy:
    """Accept only when both juniors gave the same readable answer."""
    return Policy(
        "rule-v0",
        lambda rows: [
            r.get("j1_answer") is not None and r["j1_answer"] == r.get("j2_answer") for r in rows
        ],
    )


def model_policy(meta: dict, predict: Callable[[list[list[float]]], Sequence[float]]) -> Policy:
    """A trained policy: accept when P(correct) >= its balanced threshold. As in the router,
    a request with no readable junior answer never reaches the model and is escalated."""
    threshold = float(meta["thresholds"]["balanced"])

    def decide(rows: list[dict]) -> list[bool]:
        asked = [
            i
            for i, r in enumerate(rows)
            if r.get("j1_answer") is not None or r.get("j2_answer") is not None
        ]
        probs = predict([vector(meta, rows[i]) for i in asked]) if asked else []
        accept = [False] * len(rows)
        for i, p in zip(asked, probs, strict=True):
            accept[i] = float(p) >= threshold
        return accept

    return Policy(meta["model_version"], decide)


# ---------------------------------------------------------------- comparison


def summarize(accept: list[bool], y: list[int], senior_cost: float) -> dict:
    n, n_acc = len(y), sum(accept)
    wrong = sum(1 for a, c in zip(accept, y, strict=True) if a and c == 0)
    return {
        "coverage": n_acc / n if n else 0.0,
        "accepted_accuracy": (n_acc - wrong) / n_acc if n_acc else 0.0,
        "false_accept_upper95": wilson(wrong, n_acc)[1],
        "escalations": n - n_acc,
        "senior_usd_per_1000": round(1000 * (n - n_acc) / n * senior_cost, 2) if n else 0.0,
    }


def compare(rows: list[dict], champion: Policy, challenger: Policy, senior_cost: float) -> dict:
    """Decision-by-decision comparison. A decision is 'right' when it accepts a correct junior
    answer or escalates a wrong one (assuming the senior then answers correctly)."""
    y = [int(r["junior_correct"]) for r in rows]
    a, b = champion.decide(rows), challenger.decide(rows)
    now_escalated = [i for i in range(len(rows)) if a[i] and not b[i]]
    now_accepted = [i for i in range(len(rows)) if b[i] and not a[i]]
    caught = sum(1 for i in now_escalated if y[i] == 0)  # wrong answers no longer accepted
    new_errors = sum(1 for i in now_accepted if y[i] == 0)  # wrong answers newly accepted
    better = caught + (len(now_accepted) - new_errors)  # rows where only the challenger is right
    worse = (len(now_escalated) - caught) + new_errors  # rows where only the champion is right
    ch, cl = summarize(a, y, senior_cost), summarize(b, y, senior_cost)
    verdict = decide(ch, cl, better, worse)  # the release gate's own rule (common/release_gate)
    gate = verdict["criteria"]
    return {
        "split": SPLIT,
        "n": len(rows),
        "champion": {"name": champion.name, **ch},
        "challenger": {"name": challenger.name, **cl},
        "decisions": {
            "same": len(rows) - len(now_escalated) - len(now_accepted),
            "now_escalated": len(now_escalated),
            "now_escalated_wrong_answers": caught,
            "now_accepted": len(now_accepted),
            "now_accepted_wrong_answers": new_errors,
            "challenger_right_only": better,
            "champion_right_only": worse,
            "sign_test_p": verdict["sign_test_p"],
        },
        "gate_vs_champion": gate,
        "verdict": "eligible for promotion" if verdict["passed"] else "keep the champion",
        "senior_usd_per_call": senior_cost,
    }


def render(r: dict) -> str:
    ch, cl, d = r["champion"], r["challenger"], r["decisions"]
    pct = lambda x: f"{100 * x:.1f}%"  # noqa: E731
    lines = [
        f"# Shadow comparison: {cl['name']} (challenger) vs {ch['name']} (champion)",
        "",
        f"Replayed **{r['n']}** requests of the `{r['split']}` split through both policies, using "
        "the artifacts MLServer would load and the router's feature code. Nothing was served.",
        "",
        "| Measure | Champion | Challenger |",
        "|---|---|---|",
        f"| Coverage (answered by the juniors) | {pct(ch['coverage'])} | {pct(cl['coverage'])} |",
        f"| Accuracy of accepted answers | {pct(ch['accepted_accuracy'])} | {pct(cl['accepted_accuracy'])} |",
        f"| False acceptance, upper 95% | {pct(ch['false_accept_upper95'])} | {pct(cl['false_accept_upper95'])} |",
        f"| Escalations | {ch['escalations']} | {cl['escalations']} |",
        f"| Senior cost per 1,000 requests | ${ch['senior_usd_per_1000']:.2f} | ${cl['senior_usd_per_1000']:.2f} |",
        "",
        "| Decision change | Requests | Of which the junior answer was wrong |",
        "|---|---|---|",
        f"| Same decision | {d['same']} | |",
        f"| Champion accepts, challenger escalates | {d['now_escalated']} | {d['now_escalated_wrong_answers']} |",
        f"| Champion escalates, challenger accepts | {d['now_accepted']} | {d['now_accepted_wrong_answers']} |",
        "",
        f"Where the two disagree, the challenger makes the right call {d['challenger_right_only']} "
        f"times and the champion {d['champion_right_only']} times (exact sign test p = "
        f"{d['sign_test_p']:.3f}; right call = accept a correct junior answer or escalate a wrong one).",
        "",
        "Gate against the current champion: "
        + ", ".join(f"{k} {'PASS' if v else 'FAIL'}" for k, v in r["gate_vs_champion"].items()),
        "",
        f"**Verdict: {r['verdict']}.** Promotion stays a human decision (`make promote VERSION=...`).",
        f"Senior cost assumes ${r['senior_usd_per_call']} per escalation (measured mean).",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- MLflow


def load_policy(c, ref: str):
    """(registry version, Policy) for an alias or version number; None if the alias is unset."""
    import joblib
    import mlflow
    import numpy as np
    from mlflow.exceptions import MlflowException

    try:
        mv = (
            c.get_model_version(MODEL, ref)
            if ref.isdigit()
            else c.get_model_version_by_alias(MODEL, ref)
        )
    except MlflowException:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(
            mlflow.artifacts.download_artifacts(
                run_id=mv.run_id, artifact_path="mlserver_model", dst_path=tmp
            )
        )
        meta = json.loads((pkg / "policy_meta.json").read_text())
        model = joblib.load(pkg / "model.joblib")

    def predict(xs: list[list[float]]) -> list[float]:
        return model.predict_proba(np.asarray(xs, dtype=np.float32))[:, 1].tolist()

    return mv, model_policy(meta, predict)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--challenger", default="challenger", help="alias or version number")
    ap.add_argument("--champion", default="champion", help="alias or version; unset = rule-v0")
    ap.add_argument("--out", default=str(ROOT / "results"))
    ap.add_argument("--senior-cost", type=float, default=SENIOR_COST_USD)
    a = ap.parse_args()

    from mlflow import MlflowClient

    c = MlflowClient()
    loaded = load_policy(c, a.challenger)
    if loaded is None:
        print(f"no {a.challenger}: nothing to compare (run the re-validation DAG first)")
        return 2
    chal_mv, challenger = loaded
    champ = load_policy(c, a.champion)
    champion = champ[1] if champ else rule_v0()

    rows = [json.loads(x) for x in Path(a.data).read_text().splitlines() if x.strip()]
    rows = [r for r in rows if split_of(r["group"]) == SPLIT]
    report = compare(rows, champion, challenger, a.senior_cost)
    report["challenger"]["registry_version"] = chal_mv.version
    report["champion"]["registry_version"] = champ[0].version if champ else None

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"shadow_{date.today().isoformat()}_{champion.name}_vs_{challenger.name}"
    (out / f"{stem}.json").write_text(json.dumps(report, indent=2))
    (out / f"{stem}.md").write_text(render(report))
    c.log_artifact(chal_mv.run_id, str(out / f"{stem}.md"), artifact_path="shadow")
    c.set_model_version_tag(MODEL, chal_mv.version, "shadow_verdict", report["verdict"])
    print(render(report))
    print(f"written: {out / stem}.md (also attached to the challenger's MLflow run)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
