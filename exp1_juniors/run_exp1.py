"""Experiment 1: can two local junior models on this Mac give a usable agreement signal, fast enough?

Questions this answers (for the architecture document, section I):
  Q1  Which pair of local models from different families gives the best agreement signal?
      (accuracy when they agree vs. when they disagree, false-agreement rate, escalation rate)
  Q2  What is the junior-stage latency when both run in parallel, as the router will call them?
  Q3  How does latency degrade with concurrent requests (capacity of one laptop)?
  Q4  How much memory do two loaded models take?

Subcommands
  screen  --models gemma3:4b qwen3:4b phi4-mini llama3.2:3b
          Runs every question through every model one at a time (clean per-model latency),
          then scores every possible pair.
  load    --pair gemma3:4b qwen3:4b --levels 1 4 8 --n 24
          Sends both juniors in parallel per request (like the router), at several concurrency levels.

All raw responses go to results/*.jsonl, summaries to results/*.md. Nothing is estimated:
every number in the summaries is computed from the raw files.
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common.normalizer import normalize  # noqa: E402

OLLAMA = "http://localhost:11434"
DATA = ROOT / "data" / "exp1_questions.jsonl"
RESULTS = ROOT / "results"
PROMPTS = json.loads((ROOT / "prompts" / "junior_prompts_v2.json").read_text())
LABELS_FILE = ROOT / "data" / "banking77_labels.json"
TIMEOUT_S = 120.0

# Thinking models must have thinking switched off, or one answer takes thousands of tokens.
THINKING_MODELS = ("qwen3",)


# ---------- helpers ----------------------------------------------------------------------

def load_labels() -> list[str]:
    return json.loads(LABELS_FILE.read_text(encoding="utf-8")) if LABELS_FILE.exists() else []


LABELS = load_labels()


def load_questions() -> list[dict]:
    qs = [json.loads(l) for l in DATA.read_text(encoding="utf-8").splitlines() if l.strip()]
    for q in qs:  # label questions share one category list, kept in a separate file
        if q["task_type"] == "label":
            q["options"] = "\n".join(LABELS)
    return qs


def pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def build_body(model: str, q: dict) -> dict:
    spec = PROMPTS[q["task_type"]]
    body = {
        "model": model,
        "stream": False,
        "keep_alive": "30m",
        "messages": [
            {"role": "system", "content": spec["system"]},
            {"role": "user", "content": spec["user_template"].format(
                question=q["question"], options=q["options"])},
        ],
        "options": {"temperature": 0, "seed": 42, "num_predict": spec["num_predict"], "num_ctx": 4096},
    }
    if model.startswith(THINKING_MODELS):
        body["think"] = False
    return body


async def ask(client: httpx.AsyncClient, model: str, q: dict) -> dict:
    t0 = time.perf_counter()
    rec = {"model": model, "id": q["id"], "task_type": q["task_type"], "gold": q["gold"]}
    try:
        r = await client.post(f"{OLLAMA}/api/chat", json=build_body(model, q), timeout=TIMEOUT_S)
        r.raise_for_status()
        j = r.json()
        text = j.get("message", {}).get("content", "")
        rec.update({
            "ok": True,
            "text": text,
            "answer": normalize(q["task_type"], text, LABELS),
            "eval_count": j.get("eval_count"),
            "prompt_eval_count": j.get("prompt_eval_count"),
            "server_total_ms": (j.get("total_duration") or 0) / 1e6,
            "load_ms": (j.get("load_duration") or 0) / 1e6,
        })
    except Exception as e:  # recorded, never hidden
        rec.update({"ok": False, "error": f"{type(e).__name__}: {e}", "answer": None})
    rec["wall_ms"] = (time.perf_counter() - t0) * 1000
    rec["correct"] = rec["answer"] is not None and rec["answer"] == q["gold"]
    return rec


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def env_info() -> dict:
    def run(cmd: list[str]) -> str:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
        except Exception as e:
            return f"unavailable ({e})"
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "machine": platform.machine(),
        "macos": platform.mac_ver()[0],
        "python": platform.python_version(),
        "ollama_version": run(["ollama", "--version"]),
        "ollama_ps": run(["ollama", "ps"]),
        "prompt_version": PROMPTS["version"],
        "dataset_sha256": (DATA.with_suffix(".sha256")).read_text().split()[0]
        if DATA.with_suffix(".sha256").exists() else "missing",
    }


# ---------- screen: per-model and per-pair -------------------------------------------------

async def screen(models: list[str]) -> None:
    qs = load_questions()
    rows: list[dict] = []
    async with httpx.AsyncClient() as client:
        for m in models:
            print(f"[{m}] warm-up (load time excluded from results)")
            await ask(client, m, qs[0])
            for i, q in enumerate(qs, 1):
                rows.append(await ask(client, m, q))
                if i % 20 == 0:
                    print(f"[{m}] {i}/{len(qs)}")
    info = env_info()
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    write_jsonl(RESULTS / f"exp1_screen_raw_{stamp}.jsonl", rows)

    by = {(r["model"], r["id"]): r for r in rows}
    ids = [q["id"] for q in qs]
    task = {q["id"]: q["task_type"] for q in qs}
    lines = [f"# Experiment 1a: junior screening ({stamp})", "",
             "All figures measured on this machine; none are estimates.", "",
             "```", json.dumps(info, indent=2), "```", "",
             "## Per model", "",
             "| Model | Accuracy choice | Accuracy label | Unreadable | Errors | p50 ms (choice) | p95 ms (choice) | p50 ms (label) | p95 ms (label) |",
             "|---|---|---|---|---|---|---|---|---|"]
    for m in models:
        mr = [by[(m, i)] for i in ids]
        def acc(t):
            xs = [r for r in mr if r["task_type"] == t]
            return f"{100*sum(r['correct'] for r in xs)/len(xs):.1f}%" if xs else "-"
        def lat(t, p):
            xs = [r["wall_ms"] for r in mr if r["task_type"] == t and r.get("ok")]
            return f"{pct(xs, p):.0f}" if xs else "-"
        unread = sum(1 for r in mr if r.get("ok") and r["answer"] is None)
        errs = sum(1 for r in mr if not r.get("ok"))
        lines.append(f"| {m} | {acc('choice')} | {acc('label')} | {unread} | {errs} | "
                     f"{lat('choice',50)} | {lat('choice',95)} | {lat('label',50)} | {lat('label',95)} |")

    lines += ["", "## Every pair (agreement signal)", "",
              "Agree = both answers readable and equal. 'Acc if agree' = accuracy of the agreed answer. "
              "'Acc if disagree' = accuracy of junior 1 when they disagree. Gap = the difference "
              "(proposal target: at least 20 points). False agreement = wrong although they agreed "
              "(proposal target: at most 25%).", "",
              "| Pair | Agree (= not escalated) | Acc if agree | Acc if disagree | Gap (pts) | False agreement | Choice agree acc | Label agree acc |",
              "|---|---|---|---|---|---|---|---|"]
    pair_scores = []
    for a, b in itertools.combinations(models, 2):
        agree = [i for i in ids if by[(a, i)]["answer"] is not None and by[(a, i)]["answer"] == by[(b, i)]["answer"]]
        dis = [i for i in ids if i not in set(agree)]
        acc_ag = 100 * sum(by[(a, i)]["correct"] for i in agree) / len(agree) if agree else float("nan")
        acc_dis = 100 * sum(by[(a, i)]["correct"] for i in dis) / len(dis) if dis else float("nan")
        def sub(t):
            xs = [i for i in agree if task[i] == t]
            return f"{100*sum(by[(a,i)]['correct'] for i in xs)/len(xs):.0f}% (n={len(xs)})" if xs else "-"
        gap = acc_ag - acc_dis
        pair_scores.append((gap, a, b))
        lines.append(f"| {a} + {b} | {100*len(agree)/len(ids):.0f}% | {acc_ag:.1f}% | {acc_dis:.1f}% | "
                     f"{gap:.1f} | {100-acc_ag:.1f}% | {sub('choice')} | {sub('label')} |")
    best = max(pair_scores)
    lines += ["", f"Largest gap: **{best[1]} + {best[2]}** ({best[0]:.1f} points). "
              "Choose the pair on gap AND false agreement AND latency, not gap alone.",
              "", f"n = {len(ids)} questions; with this n, a percentage is only good to roughly "
              f"±{100*1.96*(0.25/len(ids))**0.5:.0f} points (95%). Treat small differences as ties."]
    out = RESULTS / f"exp1_screen_summary_{stamp}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nsaved {out}")


# ---------- load: parallel pair under concurrency --------------------------------------------

async def pair_request(client, a, b, q) -> dict:
    t0 = time.perf_counter()
    ra, rb = await asyncio.gather(ask(client, a, q), ask(client, b, q))
    return {"id": q["id"], "task_type": q["task_type"],
            "stage_ms": (time.perf_counter() - t0) * 1000,
            "ok": ra["ok"] and rb["ok"],
            "timed_out": any("Timeout" in r.get("error", "") for r in (ra, rb))}


async def load(a: str, b: str, levels: list[int], n: int) -> None:
    qs = load_questions()
    # mix of both task types, same questions at every level
    sample = [q for q in qs if q["task_type"] == "choice"][: n // 2] + \
             [q for q in qs if q["task_type"] == "label"][: n - n // 2]
    limits = httpx.Limits(max_connections=64, max_keepalive_connections=64)
    rows, lines = [], []
    async with httpx.AsyncClient(limits=limits) as client:
        await pair_request(client, a, b, sample[0])  # warm-up, both models loaded
        info = env_info()  # `ollama ps` now shows both models resident
        for c in levels:
            sem = asyncio.Semaphore(c)
            async def one(q):
                async with sem:
                    return await pair_request(client, a, b, q)
            t0 = time.perf_counter()
            res = await asyncio.gather(*(one(q) for q in sample))
            elapsed = time.perf_counter() - t0
            for r in res:
                r["concurrency"] = c
            rows += res
            ok = [r["stage_ms"] for r in res if r["ok"]]
            lines.append(f"| {c} | {len(res)} | {100*len(ok)/len(res):.0f}% | "
                         f"{pct(ok,50):.0f} | {pct(ok,95):.0f} | {len(res)/elapsed:.2f} | "
                         f"{sum(r['timed_out'] for r in res)} |")
            print(f"concurrency {c}: done in {elapsed:.1f}s")
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    write_jsonl(RESULTS / f"exp1_load_raw_{stamp}.jsonl", rows)
    out_lines = [f"# Experiment 1b: parallel juniors under load ({stamp})", "",
                 f"Pair: {a} + {b}. Each request = both juniors called in parallel (router behaviour). "
                 f"Timeout per call {TIMEOUT_S:.0f} s. Same {len(sample)} questions at every level.", "",
                 "```", json.dumps(info, indent=2), "```", "",
                 "| Concurrent requests | Requests | Success | Stage p50 ms | Stage p95 ms | Requests/s | Timeouts |",
                 "|---|---|---|---|---|---|---|", *lines]
    out = RESULTS / f"exp1_load_summary_{stamp}.md"
    out.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print("\n".join(out_lines))
    print(f"\nsaved {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("screen")
    s.add_argument("--models", nargs="+", required=True)
    l = sub.add_parser("load")
    l.add_argument("--pair", nargs=2, required=True)
    l.add_argument("--levels", nargs="+", type=int, default=[1, 4, 8])
    l.add_argument("--n", type=int, default=24)
    args = ap.parse_args()
    RESULTS.mkdir(exist_ok=True)
    if args.cmd == "screen":
        asyncio.run(screen(args.models))
    else:
        asyncio.run(load(args.pair[0], args.pair[1], args.levels, args.n))


if __name__ == "__main__":
    main()
