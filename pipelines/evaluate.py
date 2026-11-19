"""Held-out evaluation (plan M4b): every routing strategy on the TEST split of the replay pool.

The test split (10% of the pool by group hash) is never used for training, threshold choice or
the release gate, so these are the numbers the report quotes.

  seniors --model M --tag T [--effort E] [--thinking disabled] [--max-usd 3]
      Ask a Claude model every test question through the router's own adapter, prompt builder
      and normalizer. Resumable: questions already answered are skipped.
  report [--policy-url http://localhost:8090 | --policy-probs results/eval_policy_probs_<ts>.jsonl]
      Combine the recorded junior answers, the senior answers and the CHAMPION policy's
      probabilities into results/eval_summary_<ts>.md, results/eval_curve_<ts>.csv and a paired
      comparison. The probabilities come from the running MLServer (the real serving path) and are
      saved to results/eval_policy_probs_<ts>.jsonl, so the report can be rebuilt later from that
      file with --policy-probs and no running stack.

The report is built from small pure functions (outcomes, strategies, summary statistics,
comparisons, rendering) that tests/test_evaluate.py checks, including a regression test against
the committed results; report() only reads the inputs, calls them and writes the files.

Latency per strategy is composed from measured parts (junior stage from the replay run, policy
call, senior call), not measured end to end; the summary says so.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.metrics import bootstrap_ci, percentile, wilson  # noqa: E402
from common.normalizer import normalize  # noqa: E402
from common.splits import split_of  # noqa: E402

POOL = ROOT / "data" / "replay" / "pool_v1.jsonl"
QUESTIONS = ROOT / "data" / "replay" / "pool_v1_questions.jsonl"
LABELS = ROOT / "data" / "banking77_labels.json"
PROMPTS = ROOT / "prompts" / "junior_prompts_v2.json"
META = ROOT / "policy_models" / "routing-policy" / "policy_meta.json"
RESULTS = ROOT / "results"
URL = "https://api.anthropic.com/v1/messages"
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-haiku-5-5": (0.10, 0.50)}  # USD/MTok, 10 Nov


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_rows() -> list[dict]:
    return [r for r in read_jsonl(POOL) if split_of(r["group"]) == "test"]


def senior_file(tag: str) -> Path:
    return RESULTS / f"eval_senior_{tag}.jsonl"


def api_key() -> str:
    key = os.environ.get("SENIOR_API_KEY")
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("SENIOR_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key or not key.startswith("sk-ant-"):
        raise SystemExit(
            "SENIOR_API_KEY is not set to an Anthropic key (in .env or the environment)"
        )
    return key


# ---------------------------------------------------------------- seniors


async def seniors(model: str, tag: str, effort: str | None, thinking: str | None, max_usd: float):
    import httpx

    from router.adapters import AnthropicSenior, CircuitBreaker, ModelUnavailable
    from router.prompts import build_prompt, load_prompts

    rows = test_rows()
    questions = {q["question_id"]: q for q in read_jsonl(QUESTIONS)}
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    prompts = load_prompts(PROMPTS)
    out_path = senior_file(tag)
    done = {r["question_id"] for r in read_jsonl(out_path) if r.get("ok")}
    todo = [r for r in rows if r["question_id"] not in done]
    p_in, p_out = PRICES[model]
    print(
        f"{model}: {len(rows)} test questions, {len(done)} done, {len(todo)} to go; stops at ${max_usd:.2f}"
    )
    spent, sem = [0.0], asyncio.Semaphore(4)

    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=8)) as client:
        senior = AnthropicSenior(
            client,
            URL,
            model,
            api_key(),
            timeout_s=60,
            effort=effort,
            thinking={"type": thinking} if thinking else None,
            breaker=CircuitBreaker(threshold=10),
        )

        async def one(r: dict) -> dict:
            q = questions[r["question_id"]]
            opts = q["options"] if q["task_type"] == "choice" else labels
            system, user, max_tokens = build_prompt(prompts, q["task_type"], q["question"], opts)
            base = {"question_id": r["question_id"], "model": model, "ok": False, "answer": None}
            async with sem:
                if spent[0] >= max_usd:
                    return base | {"error": "spend_limit"}
                try:
                    rep = await senior.ask(system, user, max_tokens)
                except ModelUnavailable as e:
                    return base | {"error": str(e)}
            cost = (rep.input_tokens * p_in + rep.output_tokens * p_out) / 1e6
            spent[0] += cost
            return base | {
                "ok": True,
                "error": None,
                "answer": normalize(q["task_type"], rep.text, opts),
                "input_tokens": rep.input_tokens,
                "output_tokens": rep.output_tokens,
                "cost_usd": cost,
                "latency_ms": round(rep.latency_ms, 1),
                "stop_reason": rep.stop_reason,
            }

        n = 0
        with out_path.open("a", encoding="utf-8") as f:
            for coro in asyncio.as_completed([one(r) for r in todo]):
                f.write(json.dumps(await coro) + "\n")
                f.flush()
                n += 1
                if n % 100 == 0 or n == len(todo):
                    print(f"  {n}/{len(todo)}  spent ${spent[0]:.3f}")
    print(f"done: ${spent[0]:.3f} spent in this run -> {out_path.relative_to(ROOT)}")


# ---------------------------------------------------------------- report
#
# Outcome: one question answered by one strategy, as a dict with
#   correct (0/1), cost (USD paid), esc (asked the senior), abst (no readable answer),
#   acc_junior (the junior answer was returned), ms (composed latency).

RULE = "Router, rule-v0 (agreement)"
OPUS_ALONE = "Opus 5.5 alone (premium-only)"
HAIKU_ALONE = "Haiku 5.5 alone"
JUNIOR1_ALONE = "Junior 1 alone (gemma3:12b)"
JUNIOR2_ALONE = "Junior 2 alone (phi4:14b)"
THRESHOLDS = [i / 20 for i in range(0, 21)]
CURVE_BASELINES = (RULE, OPUS_ALONE, HAIKU_ALONE, JUNIOR1_ALONE)
TABLE_HEADER = [
    "| Strategy | Accuracy (95% CI) | Choice | Label | Escalated | Abstained | False acceptance (upper 95%) | Paid cost / 1,000 | Latency p50 / p95 ms* |",
    "|---|---|---|---|---|---|---|---|---|",
]


def senior_outcome(s: dict, gold: str) -> tuple[int, float, bool]:
    """Correct (0/1), paid cost and abstained for one senior answer; a failure counts as abstained."""
    if not s["ok"] or s["answer"] is None:
        return 0, s.get("cost_usd", 0.0), True
    return int(s["answer"] == gold), s["cost_usd"], False


def pick(r: dict) -> str | None:
    """The junior answer the router returns: junior 1's, or junior 2's if junior 1's is unreadable."""
    return r["j1_answer"] if r["j1_answer"] is not None else r["j2_answer"]


def routed(r: dict, accept: bool, opus: dict[str, dict]) -> dict:
    """One routed question: the junior answer if accepted and readable, otherwise Opus."""
    gold = r["gold"]
    if accept and pick(r) is not None:
        return {
            "correct": int(pick(r) == gold),
            "cost": 0.0,
            "esc": False,
            "abst": False,
            "acc_junior": True,
            "ms": r["junior_stage_ms"] + 1.0,
        }
    s = opus[r["question_id"]]
    c, cost, abst = senior_outcome(s, gold)
    return {
        "correct": c,
        "cost": cost,
        "esc": True,
        "abst": abst,
        "acc_junior": False,
        "ms": r["junior_stage_ms"] + 1.0 + s.get("latency_ms", 0.0),
    }


def single(r: dict, s: dict) -> dict:
    """A senior model answering the question on its own."""
    c, cost, abst = senior_outcome(s, r["gold"])
    return {
        "correct": c,
        "cost": cost,
        "esc": True,
        "abst": abst,
        "acc_junior": False,
        "ms": s.get("latency_ms", 0.0),
    }


def junior(r: dict, j: str) -> dict:
    """One junior answering on its own; latency is the parallel junior stage (per-model not recorded)."""
    return {
        "correct": int(r[f"{j}_answer"] == r["gold"]),
        "cost": 0.0,
        "esc": False,
        "abst": r[f"{j}_answer"] is None,
        "acc_junior": True,
        "ms": r["junior_stage_ms"],
    }


def baseline_strategies(
    rows: list[dict], opus: dict[str, dict], haiku: dict[str, dict]
) -> dict[str, list[dict]]:
    """Every strategy that needs no routing policy, in the order of the report's table."""
    return {
        JUNIOR1_ALONE: [junior(r, "j1") for r in rows],
        JUNIOR2_ALONE: [junior(r, "j2") for r in rows],
        HAIKU_ALONE: [single(r, haiku[r["question_id"]]) for r in rows],
        OPUS_ALONE: [single(r, opus[r["question_id"]]) for r in rows],
        RULE: [routed(r, r["agreed"], opus) for r in rows],
    }


def policy_label(thr: float) -> str:
    return f"Router, policy v2 (champion, P >= {thr:.2f})"


def policy_strategy(
    rows: list[dict], opus: dict[str, dict], p: dict[str, float], thr: float
) -> list[dict]:
    """The router with the trained policy: accept the junior answer when P(correct) >= thr."""
    return [routed(r, p[r["question_id"]] >= thr, opus) for r in rows]


def summarize(outs: list[dict], rows: list[dict]) -> dict:
    """Accuracy with its Wilson interval, per task type, escalation, abstention, cost per 1,000,
    false acceptance (wrong answers among accepted junior answers) and latency percentiles."""
    n = len(rows)
    k = sum(o["correct"] for o in outs)
    acc_j = [o for o in outs if o["acc_junior"]]
    fa = sum(1 for o in acc_j if not o["correct"])
    by = {
        t: [o["correct"] for o, r in zip(outs, rows, strict=True) if r["task_type"] == t]
        for t in ("choice", "label")
    }
    ms = [o["ms"] for o in outs]
    return {
        "acc": k / n,
        "ci": wilson(k, n),
        "choice": statistics.fmean(by["choice"]) if by["choice"] else float("nan"),
        "label": statistics.fmean(by["label"]) if by["label"] else float("nan"),
        "esc": sum(o["esc"] for o in outs) / n,
        "abst": sum(o["abst"] for o in outs) / n,
        "cost_k": 1000 * sum(o["cost"] for o in outs) / n,
        "fa": (fa / len(acc_j)) if acc_j else float("nan"),
        "fa_hi": wilson(fa, len(acc_j))[1] if acc_j else float("nan"),
        "p50": percentile(ms, 50),
        "p95": percentile(ms, 95),
    }


def random_routing(rows: list[dict], opus: dict[str, dict], esc_rate: float) -> tuple[float, float]:
    """Expected accuracy and paid cost per 1,000 when a random esc_rate share goes to Opus."""
    acc_pick = statistics.fmean(int(pick(r) == r["gold"]) for r in rows)
    acc_opus = statistics.fmean(senior_outcome(opus[r["question_id"]], r["gold"])[0] for r in rows)
    cost_opus = statistics.fmean(opus[r["question_id"]].get("cost_usd", 0.0) for r in rows)
    return (1 - esc_rate) * acc_pick + esc_rate * acc_opus, 1000 * esc_rate * cost_opus


def paired(a: list[dict], b: list[dict]) -> dict:
    """Strategy a against b on the same questions: how many routing decisions differ, and the
    accuracy and cost differences with percentile-bootstrap 95% intervals."""
    n = len(a)
    acc = (sum(o["correct"] for o in a) - sum(o["correct"] for o in b)) / n
    acc_ci = bootstrap_ci(
        n, lambda idx: sum(a[i]["correct"] - b[i]["correct"] for i in idx) / len(idx)
    )
    cost_k = 1000 * (sum(o["cost"] for o in a) - sum(o["cost"] for o in b)) / n
    cost_ci = bootstrap_ci(
        n, lambda idx: 1000 * sum(a[i]["cost"] - b[i]["cost"] for i in idx) / len(idx)
    )
    differ = sum(1 for x, y in zip(a, b, strict=True) if x["esc"] != y["esc"])
    return {
        "n": n,
        "differ": differ,
        "acc": acc,
        "acc_ci": acc_ci,
        "cost_k": cost_k,
        "cost_ci": cost_ci,
    }


def threshold_curve(
    rows: list[dict], opus: dict[str, dict], p: dict[str, float], stats: dict[str, dict]
) -> list[list]:
    """CSV rows for the cost/accuracy figure: the policy at every threshold, then the baselines."""
    out = [["strategy", "threshold", "escalation_rate", "accuracy", "cost_per_1000_usd"]]
    for t in THRESHOLDS:
        s = summarize(policy_strategy(rows, opus, p, t), rows)
        out.append(["policy v2", t, round(s["esc"], 4), round(s["acc"], 4), round(s["cost_k"], 3)])
    for name in CURVE_BASELINES:
        s = stats[name]
        out.append([name, "", round(s["esc"], 4), round(s["acc"], 4), round(s["cost_k"], 3)])
    return out


def audit_check(rows: list[dict], opus: dict[str, dict], p: dict[str, float], thr: float) -> dict:
    """What the live audit sampler would measure (ADR-26): on the answers the policy accepts, how
    often Opus disagrees, against the true false-acceptance rate on the gold labels."""
    accepted = [r for r in rows if p[r["question_id"]] >= thr and pick(r) is not None]
    audited = [
        r
        for r in accepted
        if opus[r["question_id"]]["ok"] and opus[r["question_id"]]["answer"] is not None
    ]
    disagree = [r for r in audited if opus[r["question_id"]]["answer"] != pick(r)]
    return {
        "accepted": len(accepted),
        "audited": len(audited),
        "true_fa": sum(pick(r) != r["gold"] for r in accepted),
        "disagree": len(disagree),
        "opus_right": sum(opus[r["question_id"]]["answer"] == r["gold"] for r in disagree),
    }


def senior_facts(rows: list[dict], opus: dict[str, dict]) -> tuple[int, float]:
    """How often Opus disagrees with the gold label, and its mean paid cost per call."""
    wrong = sum(
        1
        for r in rows
        if opus[r["question_id"]]["ok"] and opus[r["question_id"]]["answer"] != r["gold"]
    )
    return wrong, statistics.fmean(o.get("cost_usd", 0.0) for o in opus.values() if o["ok"])


def _share(k: int, n: int) -> float:
    return k / n if n else float("nan")


def strategy_row(name: str, s: dict) -> str:
    """One row of the report's table. False acceptance applies only where junior answers are returned."""
    fa = (
        "–"
        if s["fa"] != s["fa"] or ("alone" in name and "Junior" not in name)
        else f"{s['fa']:.1%} ({s['fa_hi']:.1%})"
    )
    return (
        f"| {name} | {s['acc']:.1%} ({s['ci'][0]:.1%}–{s['ci'][1]:.1%}) | {s['choice']:.1%} | {s['label']:.1%} | "
        f"{s['esc']:.0%} | {s['abst']:.1%} | {fa} | ${s['cost_k']:.2f} | {s['p50']:.0f} / {s['p95']:.0f} |"
    )


def random_row(esc_rate: float, acc: float, cost_k: float) -> str:
    return (
        f"| Random routing (escalates {esc_rate:.0%} at random, expected value) | {acc:.1%} | | | {esc_rate:.0%} | | | "
        f"${cost_k:.2f} | |"
    )


def paired_text(c: dict) -> str:
    return (
        f"**Policy v2 vs rule-v0, paired on the same {c['n']} questions:** routing differs on {c['differ']} questions; "
        f"accuracy difference {c['acc']:+.1%} (bootstrap 95% CI {c['acc_ci'][0]:+.1%} to {c['acc_ci'][1]:+.1%}); paid cost difference "
        f"${c['cost_k']:+.2f} per 1,000 (95% CI ${c['cost_ci'][0]:+.2f} to ${c['cost_ci'][1]:+.2f}). An interval that contains 0 means the test split "
        "cannot tell the two policies apart."
    )


def audit_text(a: dict) -> str:
    lo_t, hi_t = wilson(a["true_fa"], a["accepted"])
    lo_a, hi_a = wilson(a["disagree"], a["audited"])
    return (
        f"**Audit check (ADR-26):** on the {a['accepted']} answers policy v2 accepts, the true "
        f"false-acceptance rate against gold is {_share(a['true_fa'], a['accepted']):.1%} (95% CI "
        f"{lo_t:.1%}–{hi_t:.1%}); an audit by Opus would report disagreement on "
        f"{_share(a['disagree'], a['audited']):.1%} ({lo_a:.1%}–{hi_a:.1%}). When Opus disagrees, Opus is "
        f"right {a['opus_right']}/{a['disagree']} times, which is why disagreements go to human review "
        "rather than overwriting answers."
    )


def facts_text(wrong: int, n: int, opus_cost: float) -> str:
    return (
        f"Senior facts: Opus 5.5 disagrees with the gold label on {wrong} of {n} test questions ({wrong / n:.1%}); "
        f"mean paid cost per Opus call ${opus_cost:.5f}. Abstention = the senior answered nothing readable (or failed); "
        "it counts as not correct."
    )


def render(
    rows: list[dict],
    stats: dict[str, dict],
    rand: tuple[float, float, float],
    cmp: dict,
    audit: dict,
    facts: tuple[int, float],
    curve_name: str,
    policy_name: str,
    source: str,
    generated: str,
) -> str:
    """The Markdown summary; rand is (escalation rate, expected accuracy, cost per 1,000)."""
    n = len(rows)
    lines = [
        "# Held-out evaluation on the test split (plan M4b)",
        "",
        f"Test split: **{n} questions** ({sum(r['task_type'] == 'choice' for r in rows)} MMLU choice, "
        f"{sum(r['task_type'] == 'label' for r in rows)} Banking77 label), never used for training, thresholds or the gate. "
        f"Juniors: recorded answers from replay pool v1 (junior timeout 8 s applied). Senior answers: Claude Opus 5.5 "
        f"(effort low) and Claude Haiku 5.5 (thinking off), run through the router's adapter. Policy: champion "
        f"{policy_name}, probabilities from {source}. Generated {generated}.",
        "",
        *TABLE_HEADER,
    ]
    lines += [strategy_row(name, s) for name, s in stats.items()]
    lines.append(random_row(*rand))
    lines += ["", paired_text(cmp), "", audit_text(audit)]
    lines += [
        "",
        f"Threshold sweep for the cost/accuracy figure: `{curve_name}`.",
        "",
        facts_text(facts[0], n, facts[1]),
        "",
        "*Latency is composed from measured parts: junior stage (replay run, two models in parallel at concurrency 2), "
        "policy call (~1 ms) and, when escalated, the measured Opus call. It is not an end-to-end measurement.",
    ]
    return "\n".join(lines) + "\n"


async def policy_probabilities(rows: list[dict], url: str) -> tuple[dict[str, float], str, float]:
    """P(junior answer correct) per test question from the running MLServer, through the router's
    own PolicyClient (the real serving path); also the champion's name and balanced threshold."""
    import httpx

    from router.policy import PolicyClient, PolicyMeta

    meta = PolicyMeta.load(META)
    out = {}
    async with httpx.AsyncClient() as client:
        pc = PolicyClient(client, url, meta, timeout_s=2.0)
        for r in rows:
            x = meta.vector(
                r["task_type"],
                r["n_options"],
                r["j1_answer"],
                r["j2_answer"],
                r["prompt_tokens"],
                r["j1_tokens"],
                r["j2_tokens"],
            )
            out[r["question_id"]] = await pc.p_correct(x)
    return (
        out,
        f"{meta.model_version} ({meta.feature_version}, threshold {meta.threshold_balanced:.3f})",
        meta.threshold_balanced,
    )


def save_probabilities(path: Path, p: dict[str, float], policy_name: str, thr: float) -> None:
    lines = [
        json.dumps({"question_id": q, "p_correct": v, "policy": policy_name, "threshold": thr})
        for q, v in p.items()
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_probabilities(path: Path) -> tuple[dict[str, float], str, float]:
    rows = read_jsonl(path)
    if not rows:
        raise SystemExit(f"{path} has no probabilities")
    return {r["question_id"]: r["p_correct"] for r in rows}, rows[0]["policy"], rows[0]["threshold"]


def report(policy_url: str, policy_probs: Path | None = None) -> Path:
    """Read the inputs, compute every strategy and comparison, write the summary and the curve."""
    rows = test_rows()
    opus = {r["question_id"]: r for r in read_jsonl(senior_file("opus"))}
    haiku = {r["question_id"]: r for r in read_jsonl(senior_file("haiku"))}
    missing = [
        r["question_id"]
        for r in rows
        if r["question_id"] not in opus or r["question_id"] not in haiku
    ]
    if missing:
        raise SystemExit(
            f"{len(missing)} test questions lack senior answers; run `make eval-seniors` first"
        )
    ts = datetime.now().strftime("%Y%m%d-%H%M")
    if policy_probs is not None:
        p, policy_name, thr = load_probabilities(policy_probs)
        source = f"the saved file {policy_probs.name}"
    else:
        p, policy_name, thr = asyncio.run(policy_probabilities(rows, policy_url))
        source = "the running MLServer"
        save_probabilities(RESULTS / f"eval_policy_probs_{ts}.jsonl", p, policy_name, thr)

    strategies = baseline_strategies(rows, opus, haiku)
    strategies[policy_label(thr)] = policy_strategy(rows, opus, p, thr)
    stats = {name: summarize(outs, rows) for name, outs in strategies.items()}
    esc_rule = stats[RULE]["esc"]
    rand = (esc_rule, *random_routing(rows, opus, esc_rule))
    cmp = paired(strategies[policy_label(thr)], strategies[RULE])

    curve_path = RESULTS / f"eval_curve_{ts}.csv"
    with curve_path.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(threshold_curve(rows, opus, p, stats))

    text = render(
        rows,
        stats,
        rand,
        cmp,
        audit_check(rows, opus, p, thr),
        senior_facts(rows, opus),
        curve_path.name,
        policy_name,
        source,
        datetime.now(UTC).isoformat(timespec="seconds"),
    )
    out = RESULTS / f"eval_summary_{ts}.md"
    out.write_text(text, encoding="utf-8")
    print(text, end="")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seniors")
    s.add_argument("--model", required=True, choices=sorted(PRICES))
    s.add_argument("--tag", required=True)
    s.add_argument("--effort", default="low", help="'none' to omit")
    s.add_argument("--thinking", default=None)
    s.add_argument("--max-usd", type=float, default=3.0)
    r = sub.add_parser("report")
    r.add_argument("--policy-url", default="http://localhost:8090")
    r.add_argument(
        "--policy-probs",
        type=Path,
        default=None,
        help="saved probabilities (results/eval_policy_probs_<ts>.jsonl) instead of MLServer",
    )
    a = ap.parse_args()
    if a.cmd == "seniors":
        t0 = time.perf_counter()
        asyncio.run(
            seniors(a.model, a.tag, None if a.effort == "none" else a.effort, a.thinking, a.max_usd)
        )
        print(f"wall time {time.perf_counter() - t0:.0f} s")
    else:
        report(a.policy_url, a.policy_probs)


if __name__ == "__main__":
    main()
