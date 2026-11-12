"""Held-out evaluation (plan M4b): every routing strategy on the TEST split of the replay pool.

The test split (10% of the pool by group hash) is never used for training, threshold choice or
the release gate, so these are the numbers the report quotes.

  seniors --model M --tag T [--effort E] [--thinking disabled] [--max-usd 3]
      Ask a Claude model every test question through the router's own adapter, prompt builder
      and normalizer. Resumable: questions already answered are skipped.
  report [--policy-url http://localhost:8090]
      Combine the recorded junior answers, the senior answers and the CHAMPION policy's
      probabilities (fetched from the running MLServer, i.e. the real serving path) into
      results/eval_summary_<ts>.md, results/eval_curve_<ts>.csv and a paired comparison.

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


async def policy_probabilities(rows: list[dict], url: str) -> tuple[dict[str, float], str]:
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
    )


def report(policy_url: str) -> None:
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
    p, policy_name = asyncio.run(policy_probabilities(rows, policy_url))
    from router.policy import PolicyMeta

    thr = PolicyMeta.load(META).threshold_balanced

    def senior_outcome(s: dict, gold: str) -> tuple[int, float, bool]:  # correct, cost, abstained
        if not s["ok"] or s["answer"] is None:
            return 0, s.get("cost_usd", 0.0), True
        return int(s["answer"] == gold), s["cost_usd"], False

    def pick(r):
        return r["j1_answer"] if r["j1_answer"] is not None else r["j2_answer"]

    def route(r, accept: bool) -> dict:
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
        c, cost, abst = senior_outcome(opus[r["question_id"]], gold)
        return {
            "correct": c,
            "cost": cost,
            "esc": True,
            "abst": abst,
            "acc_junior": False,
            "ms": r["junior_stage_ms"] + 1.0 + opus[r["question_id"]].get("latency_ms", 0.0),
        }

    def single(r, s: dict, ms: float | None = None) -> dict:
        c, cost, abst = senior_outcome(s, r["gold"])
        return {
            "correct": c,
            "cost": cost,
            "esc": True,
            "abst": abst,
            "acc_junior": False,
            "ms": s.get("latency_ms", 0.0) if ms is None else ms,
        }

    def junior(r, j: str) -> dict:
        return {
            "correct": int(r[f"{j}_answer"] == r["gold"]),
            "cost": 0.0,
            "esc": False,
            "abst": r[f"{j}_answer"] is None,
            "acc_junior": True,
            "ms": r["junior_stage_ms"],
        }

    strategies = {
        "Junior 1 alone (gemma3:12b)": [junior(r, "j1") for r in rows],
        "Junior 2 alone (phi4:14b)": [junior(r, "j2") for r in rows],
        "Haiku 5.5 alone": [single(r, haiku[r["question_id"]]) for r in rows],
        "Opus 5.5 alone (premium-only)": [single(r, opus[r["question_id"]]) for r in rows],
        "Router, rule-v0 (agreement)": [route(r, r["agreed"]) for r in rows],
        f"Router, policy v2 (champion, P >= {thr:.2f})": [
            route(r, p[r["question_id"]] >= thr) for r in rows
        ],
    }
    n = len(rows)
    rule = strategies["Router, rule-v0 (agreement)"]
    esc_rule = sum(o["esc"] for o in rule) / n
    # Random routing at the same escalation rate as rule-v0: expected value, computed exactly.
    acc_pick = statistics.fmean(int(pick(r) == r["gold"]) for r in rows)
    acc_opus = statistics.fmean(senior_outcome(opus[r["question_id"]], r["gold"])[0] for r in rows)
    cost_opus = statistics.fmean(opus[r["question_id"]].get("cost_usd", 0.0) for r in rows)

    def summary(outs: list[dict]) -> dict:
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
            "choice": statistics.fmean(by["choice"]),
            "label": statistics.fmean(by["label"]),
            "esc": sum(o["esc"] for o in outs) / n,
            "abst": sum(o["abst"] for o in outs) / n,
            "cost_k": 1000 * sum(o["cost"] for o in outs) / n,
            "fa": (fa / len(acc_j)) if acc_j else float("nan"),
            "fa_hi": wilson(fa, len(acc_j))[1] if acc_j else float("nan"),
            "p50": percentile(ms, 50),
            "p95": percentile(ms, 95),
        }

    ts = datetime.now().strftime("%Y%m%d-%H%M")
    lines = [
        "# Held-out evaluation on the test split (plan M4b)",
        "",
        f"Test split: **{n} questions** ({sum(r['task_type'] == 'choice' for r in rows)} MMLU choice, "
        f"{sum(r['task_type'] == 'label' for r in rows)} Banking77 label), never used for training, thresholds or the gate. "
        f"Juniors: recorded answers from replay pool v1 (junior timeout 8 s applied). Senior answers: Claude Opus 5.5 "
        f"(effort low) and Claude Haiku 5.5 (thinking off), run through the router's adapter. Policy: champion "
        f"{policy_name}, probabilities from the running MLServer. Generated {datetime.now(UTC).isoformat(timespec='seconds')}.",
        "",
        "| Strategy | Accuracy (95% CI) | Choice | Label | Escalated | Abstained | False acceptance (upper 95%) | Paid cost / 1,000 | Latency p50 / p95 ms* |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    stats = {}
    for name, outs in strategies.items():
        s = stats[name] = summary(outs)
        fa = (
            "–"
            if s["fa"] != s["fa"] or "alone" in name and "Junior" not in name
            else f"{s['fa']:.1%} ({s['fa_hi']:.1%})"
        )
        lines.append(
            f"| {name} | {s['acc']:.1%} ({s['ci'][0]:.1%}–{s['ci'][1]:.1%}) | {s['choice']:.1%} | {s['label']:.1%} | "
            f"{s['esc']:.0%} | {s['abst']:.1%} | {fa} | ${s['cost_k']:.2f} | {s['p50']:.0f} / {s['p95']:.0f} |"
        )
    rand_acc = (1 - esc_rule) * acc_pick + esc_rule * acc_opus
    lines.append(
        f"| Random routing (escalates {esc_rule:.0%} at random, expected value) | {rand_acc:.1%} | | | {esc_rule:.0%} | | | "
        f"${1000 * esc_rule * cost_opus:.2f} | |"
    )

    # paired comparison: policy v2 vs rule-v0 on the same questions
    v2 = strategies[f"Router, policy v2 (champion, P >= {thr:.2f})"]
    diff_acc = (sum(o["correct"] for o in v2) - sum(o["correct"] for o in rule)) / n
    lo, hi = bootstrap_ci(
        n, lambda idx: sum(v2[i]["correct"] - rule[i]["correct"] for i in idx) / len(idx)
    )
    dcost = 1000 * (sum(o["cost"] for o in v2) - sum(o["cost"] for o in rule)) / n
    clo, chi = bootstrap_ci(
        n, lambda idx: 1000 * sum(v2[i]["cost"] - rule[i]["cost"] for i in idx) / len(idx)
    )
    differ = sum(1 for a, b in zip(v2, rule, strict=True) if a["esc"] != b["esc"])
    lines += [
        "",
        f"**Policy v2 vs rule-v0, paired on the same {n} questions:** routing differs on {differ} questions; "
        f"accuracy difference {diff_acc:+.1%} (bootstrap 95% CI {lo:+.1%} to {hi:+.1%}); paid cost difference "
        f"${dcost:+.2f} per 1,000 (95% CI ${clo:+.2f} to ${chi:+.2f}). An interval that contains 0 means the test split "
        "cannot tell the two policies apart.",
    ]

    # threshold sweep (the curve for the report's cost/accuracy figure)
    curve_path = RESULTS / f"eval_curve_{ts}.csv"
    with curve_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["strategy", "threshold", "escalation_rate", "accuracy", "cost_per_1000_usd"])
        for t in [i / 20 for i in range(0, 21)]:
            outs = [route(r, p[r["question_id"]] >= t) for r in rows]
            s = summary(outs)
            w.writerow(
                ["policy v2", t, round(s["esc"], 4), round(s["acc"], 4), round(s["cost_k"], 3)]
            )
        for name in (
            "Router, rule-v0 (agreement)",
            "Opus 5.5 alone (premium-only)",
            "Haiku 5.5 alone",
            "Junior 1 alone (gemma3:12b)",
        ):
            s = stats[name]
            w.writerow([name, "", round(s["esc"], 4), round(s["acc"], 4), round(s["cost_k"], 3)])
    # What the live audit sampler would measure (ADR-26): on the answers policy v2 accepts, how
    # often Opus disagrees, versus the true false-acceptance rate against the gold labels.
    accepted = [r for r in rows if p[r["question_id"]] >= thr and pick(r) is not None]
    audited = [
        r
        for r in accepted
        if opus[r["question_id"]]["ok"] and opus[r["question_id"]]["answer"] is not None
    ]
    true_fa = sum(pick(r) != r["gold"] for r in accepted)
    disagree = [r for r in audited if opus[r["question_id"]]["answer"] != pick(r)]
    opus_right = sum(opus[r["question_id"]]["answer"] == r["gold"] for r in disagree)
    lo_t, hi_t = wilson(true_fa, len(accepted))
    lo_a, hi_a = wilson(len(disagree), len(audited))
    lines += [
        "",
        f"**Audit check (ADR-26):** on the {len(accepted)} answers policy v2 accepts, the true "
        f"false-acceptance rate against gold is {true_fa / len(accepted):.1%} (95% CI "
        f"{lo_t:.1%}–{hi_t:.1%}); an audit by Opus would report disagreement on "
        f"{len(disagree) / len(audited):.1%} ({lo_a:.1%}–{hi_a:.1%}). When Opus disagrees, Opus is "
        f"right {opus_right}/{len(disagree)} times, which is why disagreements go to human review "
        "rather than overwriting answers.",
    ]
    wrong = sum(
        1
        for r in rows
        if opus[r["question_id"]]["ok"] and opus[r["question_id"]]["answer"] != r["gold"]
    )
    opus_cost = statistics.fmean(o.get("cost_usd", 0.0) for o in opus.values() if o["ok"])
    lines += [
        "",
        f"Threshold sweep for the cost/accuracy figure: `{curve_path.name}`.",
        "",
        f"Senior facts: Opus 5.5 disagrees with the gold label on {wrong} of {n} test questions ({wrong / n:.1%}); "
        f"mean paid cost per Opus call ${opus_cost:.5f}. Abstention = the senior answered nothing readable (or failed); "
        "it counts as not correct.",
        "",
        "*Latency is composed from measured parts: junior stage (replay run, two models in parallel at concurrency 2), "
        "policy call (~1 ms) and, when escalated, the measured Opus call. It is not an end-to-end measurement.",
    ]
    out = RESULTS / f"eval_summary_{ts}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


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
    a = ap.parse_args()
    if a.cmd == "seniors":
        t0 = time.perf_counter()
        asyncio.run(
            seniors(a.model, a.tag, None if a.effort == "none" else a.effort, a.thinking, a.max_usd)
        )
        print(f"wall time {time.perf_counter() - t0:.0f} s")
    else:
        report(a.policy_url)


if __name__ == "__main__":
    main()
