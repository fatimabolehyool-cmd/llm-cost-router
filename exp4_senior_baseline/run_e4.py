"""Experiment 4 (architecture feedback A4): how good and how costly is the senior, and is a cheap
model alone already good enough?

Runs Claude Opus 5.5 (the senior) and Claude Haiku 5.5 (the "cheap model alone" baseline) on
the same 200 questions as Experiment 1, through the router's own adapter, prompt builder and
answer normalizer. `summarize` then compares them with the two juniors and with the router's
agreement rule on exactly the same questions, using E1's recorded junior answers.

  smoke [--record]     one real call to the configured senior; --record saves a scrubbed
                       response as the adapter's test fixture (tests/fixtures/)
  run --model M --tag T [--effort E] [--thinking disabled] [--max-usd 3]
  summarize            Markdown comparison from the newest raw file per tag

Costs are computed from the token counts the API reports, at the list prices below (checked on
the pricing page on 10 Nov 2026). Thinking tokens are billed as output tokens.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.normalizer import normalize  # noqa: E402
from router.adapters import AnthropicSenior, CircuitBreaker, ModelUnavailable  # noqa: E402
from router.prompts import build_prompt, load_prompts  # noqa: E402

PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-haiku-5-5": (0.10, 0.50)}  # USD per MTok in/out
URL = "https://api.anthropic.com/v1/messages"
QUESTIONS = ROOT / "data" / "exp1_questions.jsonl"
LABELS = ROOT / "data" / "banking77_labels.json"
PROMPTS = ROOT / "prompts" / "junior_prompts_v2.json"
JUNIOR_RAW = ROOT / "results" / "exp1_screen_raw_20261024-1133.jsonl"  # mid-size screen, E1
JUNIORS = ("gemma3:12b", "phi4:14b")  # ADR-7
RESULTS = ROOT / "results"
FIXTURE = ROOT / "tests" / "fixtures" / "anthropic_messages_response.json"


def api_key() -> str:
    """SENIOR_API_KEY from the environment or from .env; never printed."""
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


def load_questions() -> list[dict]:
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    out = []
    for line in QUESTIONS.read_text(encoding="utf-8").splitlines():
        q = json.loads(line)
        if q["task_type"] == "choice":  # stored as "A. ...\nB. ..." in E1
            q["option_list"] = [o.strip() for o in re.split(r"(?m)^[ABCD]\. ", q["options"])[1:]]
            assert len(q["option_list"]) == 4, q["id"]
        else:
            q["option_list"] = labels
        out.append(q)
    return out


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    p_in, p_out = PRICES[model]
    return (tokens_in * p_in + tokens_out * p_out) / 1e6


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (c - h, c + h)


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    k = (len(xs) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


# ---------------------------------------------------------------- smoke


async def smoke(record: bool) -> None:
    import httpx

    captured: dict = {}

    async def keep(response: httpx.Response) -> None:
        await response.aread()
        captured["json"] = response.json() if response.status_code == 200 else None

    prompts = load_prompts(PROMPTS)
    opts = ["Venus", "Mercury", "Earth", "Mars"]
    system, user, max_tokens = build_prompt(
        prompts, "choice", "Which planet is closest to the Sun?", opts
    )
    async with httpx.AsyncClient(event_hooks={"response": [keep]}) as client:
        senior = AnthropicSenior(client, URL, "claude-opus-5-5", api_key(), timeout_s=30)
        try:
            reply = await senior.ask(system, user, max_tokens)
        except ModelUnavailable as e:
            raise SystemExit(
                f"senior call failed: {e} (check the key, credits and model name)"
            ) from None
    answer = normalize("choice", reply.text, opts)
    print(
        f"answer={answer!r} (expected 'B')  stop_reason={reply.stop_reason}  "
        f"tokens in/out={reply.input_tokens}/{reply.output_tokens}  "
        f"cost=${cost_usd('claude-opus-5-5', reply.input_tokens, reply.output_tokens):.5f}  "
        f"latency={reply.latency_ms:.0f} ms"
    )
    if record and captured.get("json"):
        j = captured["json"]
        j["id"] = "msg_recorded"
        for block in j.get("content", []):
            if "signature" in block:
                block["signature"] = "sig_recorded"
        FIXTURE.write_text(json.dumps(j, indent=2) + "\n", encoding="utf-8")
        print(f"recorded a scrubbed response to {FIXTURE.relative_to(ROOT)}")


# ---------------------------------------------------------------- run


async def run(
    model: str, tag: str, effort: str | None, thinking: str | None, max_usd: float, concurrency: int
) -> None:
    import httpx

    questions = load_questions()
    prompts = load_prompts(PROMPTS)
    p_in, p_out = PRICES[model]
    headroom = 1024
    worst = sum(
        (
            len(build_prompt(prompts, q["task_type"], q["question"], q["option_list"])[1])
            / 3
            * p_in
            + (32 + headroom) * p_out
        )
        / 1e6
        for q in questions
    )
    print(
        f"{model}: {len(questions)} questions; worst case if every answer used the whole thinking "
        f"allowance ${worst:.2f}; the run stops once actual spend reaches ${max_usd:.2f}"
    )
    spent = [0.0]

    ts = datetime.now().strftime("%Y%m%d-%H%M")
    out_path = RESULTS / f"e4_raw_{tag}_{ts}.jsonl"
    sem = asyncio.Semaphore(concurrency)
    rows: list[dict] = []
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=concurrency * 2)) as client:
        senior = AnthropicSenior(
            client,
            URL,
            model,
            api_key(),
            timeout_s=60,
            effort=effort,
            thinking={"type": thinking} if thinking else None,
            thinking_headroom=headroom,
            breaker=CircuitBreaker(threshold=10),
        )

        async def one(q: dict) -> dict:
            system, user, max_tokens = build_prompt(
                prompts, q["task_type"], q["question"], q["option_list"]
            )
            failed = {
                "id": q["id"],
                "task_type": q["task_type"],
                "gold": q["gold"],
                "ok": False,
                "answer": None,
                "correct": False,
            }
            async with sem:
                if spent[0] >= max_usd:  # spend limit reached: stop sending paid calls
                    return failed | {"error": "spend_limit"}
                try:
                    r = await senior.ask(system, user, max_tokens)
                except ModelUnavailable as e:
                    return failed | {"error": str(e)}
            spent[0] += cost_usd(model, r.input_tokens, r.output_tokens)
            ans = normalize(q["task_type"], r.text, q["option_list"])
            return {
                "id": q["id"],
                "task_type": q["task_type"],
                "gold": q["gold"],
                "ok": True,
                "error": None,
                "text": r.text[:200],
                "answer": ans,
                "correct": ans == q["gold"],
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cost_usd": cost_usd(model, r.input_tokens, r.output_tokens),
                "latency_ms": round(r.latency_ms, 1),
                "stop_reason": r.stop_reason,
            }

        t0 = time.perf_counter()
        for coro in asyncio.as_completed([one(q) for q in questions]):
            rows.append(await coro)
            if len(rows) % 50 == 0:
                print(f"  {len(rows)}/{len(questions)}")
    meta = {
        "model": model,
        "tag": tag,
        "effort": effort,
        "thinking": thinking,
        "api_version": "2026-10-21",
        "prices_usd_per_mtok": [p_in, p_out],
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "wall_s": round(time.perf_counter() - t0, 1),
    }
    rows.sort(key=lambda r: r["id"])
    out_path.write_text(
        "".join(json.dumps(r | {"meta": meta}) + "\n" for r in rows), encoding="utf-8"
    )
    ok = [r for r in rows if r["ok"]]
    print(
        f"wrote results/{out_path.name}: accuracy {sum(r['correct'] for r in rows)}/{len(rows)}, "
        f"errors {len(rows) - len(ok)}, total cost ${sum(r['cost_usd'] for r in ok):.4f}"
    )


# ---------------------------------------------------------------- summarize


def newest(tag: str) -> Path:
    files = sorted(RESULTS.glob(f"e4_raw_{tag}_*.jsonl"))
    if not files:
        raise SystemExit(f"no results/e4_raw_{tag}_*.jsonl; run `run --tag {tag}` first")
    return files[-1]


def summarize() -> None:
    def read(path: Path) -> dict[str, dict]:
        return {r["id"]: r for r in map(json.loads, path.read_text(encoding="utf-8").splitlines())}

    opus, haiku = read(newest("opus")), read(newest("haiku"))
    jr = [json.loads(x) for x in JUNIOR_RAW.read_text(encoding="utf-8").splitlines()]
    j = {m: {r["id"]: r for r in jr if r["model"] == m} for m in JUNIORS}
    ids = sorted(opus)
    assert set(ids) == set(haiku) == set(j[JUNIORS[0]]) == set(j[JUNIORS[1]]), (
        "question sets differ"
    )
    task = {i: opus[i]["task_type"] for i in ids}
    opus_cost = {i: opus[i].get("cost_usd", 0.0) for i in ids}

    def agree(i: str) -> bool:
        a1, a2 = j[JUNIORS[0]][i].get("answer"), j[JUNIORS[1]][i].get("answer")
        return a1 is not None and a1 == a2

    strategies = {  # id -> (correct, paid cost, escalated)
        f"Junior alone ({JUNIORS[0]})": {i: (j[JUNIORS[0]][i]["correct"], 0.0, False) for i in ids},
        f"Junior alone ({JUNIORS[1]})": {i: (j[JUNIORS[1]][i]["correct"], 0.0, False) for i in ids},
        "Haiku 5.5 alone": {
            i: (haiku[i]["correct"], haiku[i].get("cost_usd", 0.0), False) for i in ids
        },
        "Opus 5.5 alone (premium-only)": {i: (opus[i]["correct"], opus_cost[i], True) for i in ids},
        "Router, balanced (rule-v0)": {
            i: (
                (j[JUNIORS[0]][i]["correct"], 0.0, False)
                if agree(i)
                else (opus[i]["correct"], opus_cost[i], True)
            )
            for i in ids
        },
    }
    lines = [
        "# Experiment 4: senior quality and cost, and the cheap-model baseline (feedback A4)",
        "",
        f"Same 200 questions as Experiment 1 (100 MMLU choice, 100 Banking77 label). Junior answers are "
        f"E1's recorded answers ({JUNIOR_RAW.name}); senior answers from `{newest('opus').name}` and "
        f"`{newest('haiku').name}`. All figures measured; costs from reported tokens at list prices.",
        "",
        "| Strategy | Accuracy (95% CI) | Choice | Label | Escalated | Paid cost per 1,000 questions |",
        "|---|---|---|---|---|---|",
    ]
    for name, res in strategies.items():
        k = sum(res[i][0] for i in ids)
        lo, hi = wilson(k, len(ids))
        by = {t: [res[i][0] for i in ids if task[i] == t] for t in ("choice", "label")}
        esc = sum(res[i][2] for i in ids) / len(ids)
        cost_k = 1000 * sum(res[i][1] for i in ids) / len(ids)
        lines.append(
            f"| {name} | {k / len(ids):.1%} ({lo:.1%}–{hi:.1%}) | {statistics.fmean(by['choice']):.0%} | "
            f"{statistics.fmean(by['label']):.0%} | {esc:.0%} | ${cost_k:.2f} |"
        )
    lines += [
        "",
        "**Senior calls**",
        "",
        "| Model | Errors | Unreadable | Mean tokens in / out | p50 / p95 latency ms | Cost per call |",
        "|---|---|---|---|---|---|",
    ]
    for name, res in (("Opus 5.5", opus), ("Haiku 5.5", haiku)):
        ok = [r for r in res.values() if r["ok"]]
        unread = sum(r["answer"] is None for r in ok)
        lat = [r["latency_ms"] for r in ok]
        lines.append(
            f"| {name} | {len(res) - len(ok)} | {unread} | "
            f"{statistics.fmean(r['input_tokens'] for r in ok):.0f} / {statistics.fmean(r['output_tokens'] for r in ok):.0f} | "
            f"{pct(lat, 50):.0f} / {pct(lat, 95):.0f} | ${statistics.fmean(r['cost_usd'] for r in ok):.5f} |"
        )
    wrong = [i for i in ids if opus[i]["ok"] and not opus[i]["correct"]]
    lines += [
        "",
        f"**The senior is not ground truth:** Opus 5.5 disagreed with the gold label on {len(wrong)} of "
        f"{len(ids)} questions ({', '.join(wrong[:12])}{' …' if len(wrong) > 12 else ''}). These count as senior "
        "errors; senior answers never replace gold labels (label provenance, architecture section E).",
        "",
        "Limitations: one run per model (Opus 5.5 and Haiku 5.5 accept no temperature setting, so repeated runs can "
        "differ); 200 questions give wide intervals; the junior cost is local hardware and electricity, not counted "
        "as paid cost; these 200 questions were also used to choose the junior pair (E1), which can flatter the juniors.",
    ]
    out = RESULTS / f"e4_summary_{datetime.now().strftime('%Y%m%d-%H%M')}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("smoke")
    s.add_argument("--record", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("--model", required=True, choices=sorted(PRICES))
    r.add_argument("--tag", required=True)
    r.add_argument("--effort", default="low", help="'none' to omit")
    r.add_argument("--thinking", default=None, help="e.g. 'disabled' (Haiku 5.5 only)")
    r.add_argument("--max-usd", type=float, default=2.0)
    r.add_argument("--concurrency", type=int, default=4)
    sub.add_parser("summarize")
    a = ap.parse_args()
    if a.cmd == "smoke":
        asyncio.run(smoke(a.record))
    elif a.cmd == "run":
        effort = None if a.effort == "none" else a.effort
        asyncio.run(run(a.model, a.tag, effort, a.thinking, a.max_usd, a.concurrency))
    else:
        summarize()


if __name__ == "__main__":
    main()
