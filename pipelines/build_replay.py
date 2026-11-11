"""Build the REAL replay pool: benchmark questions answered by both local juniors.

This replaces the synthetic fixture (pipelines/make_fixture.py) as the training and evaluation
data for the routing policy. It runs on the Mac host, because the juniors run in Ollama there.

Subcommands (run in this order; each is deterministic or resumable):

  prepare   Sample questions from MMLU (choice) and Banking77 (label) with a fixed seed.
            Excludes the 200 Experiment-1 questions, which were used to choose the junior pair
            (model selection must not leak into evaluation). Writes the question file + SHA-256.
  run       Ask both juniors every question, two models in parallel per question, with the
            router's own prompt builder, adapter and normalizer (no train/serve skew).
            Appends one JSON line per question; re-running skips questions already answered by
            both juniors, so an interrupted run resumes where it stopped.
  assemble  Join questions and junior answers into the replay schema read by
            pipelines/revalidate.py, plus the fields the evaluation needs. Writes file + SHA-256.

Splits are NOT decided here: revalidate.py assigns train / validation / release-evaluation /
test by hashing the `group` key, so the same question always lands in the same split.

Labels: `gold` (the benchmark answer). The senior's answers are added later and never replace
gold (architecture section E, label provenance).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.normalizer import normalize  # noqa: E402
from common.policy_features import junior_pick  # noqa: E402  (shared with the router)

DATA = ROOT / "data"
REPLAY = DATA / "replay"
E1_QUESTIONS = DATA / "exp1_questions.jsonl"
LABELS_FILE = DATA / "banking77_labels.json"
PROMPTS_FILE = ROOT / "prompts" / "junior_prompts_v2.json"
QUESTIONS_OUT = REPLAY / "pool_v1_questions.jsonl"
ANSWERS_OUT = REPLAY / "pool_v1_juniors_raw.jsonl"
POOL_OUT = REPLAY / "pool_v1.jsonl"
SEED = 2026
DEFAULT_JUNIORS = ("gemma3:12b", "phi4:14b")  # ADR-7, chosen in Experiment 1


# ---------------------------------------------------------------- pure helpers (unit-tested)


def group_key(task_type: str, question: str) -> str:
    """Normalized question text: copies that differ only in case, spacing or punctuation share
    a group, so they can never sit in two different splits."""
    text = re.sub(r"['\u2019]", "", question.lower())  # "hasn't" and "hasnt" are the same
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return f"{task_type}:" + " ".join(text.split())


def write_with_checksum(path: Path, rows: list[dict]) -> str:
    """Write JSON lines and a `.sha256` file next to them; return the digest (data version)."""
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    path.with_suffix(".sha256").write_text(f"{digest}  {path.name}\n")
    return digest


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def options_for(q: dict, labels: list[str]) -> list[str]:
    """Allowed answers: the four option texts (choice) or the shared category list (label)."""
    return q["options"] if q["task_type"] == "choice" else labels


def to_replay_row(q: dict, ans: dict, timeout_ms: float) -> dict:
    """One replay row in the schema of pipelines/revalidate.py, plus evaluation fields.

    An answer slower than the router's junior timeout counts as unavailable, because the
    router would not have waited for it.
    """

    def usable(j: str) -> str | None:
        if not ans[f"{j}_ok"] or ans[f"{j}_ms"] > timeout_ms:
            return None
        return ans[f"{j}_answer"]

    a1, a2 = usable("j1"), usable("j2")
    pick = junior_pick(a1, a2)
    gold = q["gold"]
    return {
        "question_id": q["question_id"],
        "group": q["group"],
        "task_type": q["task_type"],
        "subject": q["subject"],
        "n_options": q["n_options"],
        "agreed": a1 is not None and a1 == a2,
        "j1_ok": a1 is not None,
        "j2_ok": a2 is not None,
        "prompt_tokens": ans["prompt_tokens"],
        "j1_tokens": ans["j1_tokens"],
        "j2_tokens": ans["j2_tokens"],
        "junior_correct": int(pick is not None and pick == gold),
        "label_source": "gold",
        "gold": gold,
        "j1_answer": a1,
        "j2_answer": a2,
        "j1_correct": int(a1 == gold),
        "j2_correct": int(a2 == gold),
        "junior_stage_ms": max(ans["j1_ms"], ans["j2_ms"]),
    }


# ---------------------------------------------------------------- prepare


def prepare(n_choice: int, n_label: int) -> None:
    from datasets import concatenate_datasets, load_dataset  # host-only dependency

    e1_ids = {q["id"] for q in read_jsonl(E1_QUESTIONS)}
    labels = json.loads(LABELS_FILE.read_text(encoding="utf-8"))
    rng = random.Random(SEED)
    rows: list[dict] = []

    mmlu = load_dataset("cais/mmlu", "all", split="test")
    candidates = [i for i in range(len(mmlu)) if f"mmlu-{i}" not in e1_ids]
    for i in sorted(rng.sample(candidates, n_choice)):
        r = mmlu[i]
        rows.append(
            {
                "question_id": f"mmlu-test-{i}",
                "group": group_key("choice", r["question"]),
                "task_type": "choice",
                "subject": r["subject"],
                "question": r["question"],
                "options": list(r["choices"]),
                "n_options": 4,
                "gold": "ABCD"[r["answer"]],
            }
        )

    # Banking77: train and test are both usable, the juniors are never trained on them.
    # Experiment 1 used test indices, so those are excluded by index.
    b77_test = load_dataset("mteb/banking77", split="test")
    b77_train = load_dataset("mteb/banking77", split="train")
    b77 = concatenate_datasets([b77_test, b77_train])
    n_test = len(b77_test)
    candidates = [i for i in range(len(b77)) if not (i < n_test and f"banking77-{i}" in e1_ids)]
    for i in sorted(rng.sample(candidates, n_label)):
        r = b77[i]
        if r["label_text"] not in labels:
            raise ValueError(f"unknown Banking77 category {r['label_text']!r}")
        source = f"test-{i}" if i < n_test else f"train-{i - n_test}"
        rows.append(
            {
                "question_id": f"banking77-{source}",
                "group": group_key("label", r["text"]),
                "task_type": "label",
                "subject": "banking77",
                "question": r["text"],
                "options": None,  # the 77 categories are shared: data/banking77_labels.json
                "n_options": len(labels),
                "gold": r["label_text"],
            }
        )

    digest = write_with_checksum(QUESTIONS_OUT, rows)
    print(f"wrote {len(rows)} questions ({n_choice} choice, {n_label} label) to {QUESTIONS_OUT}")
    print(f"excluded {len(e1_ids)} Experiment-1 questions; sha256 {digest}")


# ---------------------------------------------------------------- run


async def run(juniors: tuple[str, str], ollama_url: str, concurrency: int, timeout_s: float):
    import httpx

    from router.adapters import CircuitBreaker, ModelUnavailable, OllamaJunior
    from router.prompts import build_prompt, load_prompts

    questions = read_jsonl(QUESTIONS_OUT)
    if not questions:
        raise SystemExit(f"no questions at {QUESTIONS_OUT}; run `prepare` first")
    labels = json.loads(LABELS_FILE.read_text(encoding="utf-8"))
    prompts = load_prompts(PROMPTS_FILE)
    done = {a["question_id"] for a in read_jsonl(ANSWERS_OUT) if a["j1_ok"] and a["j2_ok"]}
    todo = [q for q in questions if q["question_id"] not in done]
    print(f"{len(questions)} questions, {len(done)} already answered, {len(todo)} to go")
    if not todo:
        return

    # Batch runs never trip the breaker; a dead Ollama shows up as error rows to re-run.
    never_open = CircuitBreaker(threshold=10**9)
    sem = asyncio.Semaphore(concurrency)
    t_start, finished = time.perf_counter(), 0

    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=16)) as client:
        models = [
            OllamaJunior(client, ollama_url, m, timeout_s, breaker=never_open) for m in juniors
        ]

        async def one(q: dict) -> dict:
            opts = options_for(q, labels)
            system, user, max_tokens = build_prompt(prompts, q["task_type"], q["question"], opts)

            async def ask(m: OllamaJunior) -> dict:
                t0 = time.perf_counter()
                try:
                    rep = await m.ask(system, user, max_tokens)
                except ModelUnavailable as e:
                    ms = (time.perf_counter() - t0) * 1000
                    return {"ok": False, "error": str(e), "ms": ms, "text": None, "p": 0, "o": 0}
                return {
                    "ok": True,
                    "error": None,
                    "ms": rep.latency_ms,
                    "text": rep.text,
                    "p": rep.input_tokens,
                    "o": rep.output_tokens,
                }

            async with sem:
                r1, r2 = await asyncio.gather(ask(models[0]), ask(models[1]))
            row = {"question_id": q["question_id"], "juniors": list(juniors)}
            for tag, r in (("j1", r1), ("j2", r2)):
                row |= {
                    f"{tag}_ok": r["ok"],
                    f"{tag}_error": r["error"],
                    f"{tag}_ms": round(r["ms"], 1),
                    f"{tag}_text": r["text"],  # benchmark text only: no personal data here
                    f"{tag}_answer": normalize(q["task_type"], r["text"], opts)
                    if r["ok"]
                    else None,
                    f"{tag}_tokens": r["o"],
                }
            row["prompt_tokens"] = r1["p"] or r2["p"]
            return row

        ANSWERS_OUT.parent.mkdir(parents=True, exist_ok=True)
        with ANSWERS_OUT.open("a", encoding="utf-8") as out:
            for coro in asyncio.as_completed([one(q) for q in todo]):
                row = await coro
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                out.flush()
                finished += 1
                if finished % 100 == 0 or finished == len(todo):
                    rate = finished / (time.perf_counter() - t_start)
                    eta_min = (len(todo) - finished) / rate / 60
                    print(f"{finished}/{len(todo)}  {rate:.2f} q/s  ~{eta_min:.0f} min left")


# ---------------------------------------------------------------- assemble


def assemble(timeout_s: float) -> None:
    questions = {q["question_id"]: q for q in read_jsonl(QUESTIONS_OUT)}
    answers: dict[str, dict] = {}
    for a in read_jsonl(ANSWERS_OUT):  # the last attempt per question wins (resumed runs)
        answers[a["question_id"]] = a
    missing = [qid for qid in questions if qid not in answers]
    errors = [qid for qid, a in answers.items() if not (a["j1_ok"] and a["j2_ok"])]
    rows = [
        to_replay_row(questions[qid], answers[qid], timeout_s * 1000)
        for qid in questions
        if qid in answers
    ]
    digest = write_with_checksum(POOL_OUT, rows)
    by_task: dict[str, list[dict]] = {}
    for r in rows:
        by_task.setdefault(r["task_type"], []).append(r)
    print(f"wrote {len(rows)} replay rows to {POOL_OUT}; sha256 {digest}")
    print(f"missing (never answered): {len(missing)}; with a junior error: {len(errors)}")
    for t, rs in sorted(by_task.items()):
        agree = sum(r["agreed"] for r in rs) / len(rs)
        acc = sum(r["junior_correct"] for r in rs) / len(rs)
        print(f"  {t}: n={len(rs)}  agreement={agree:.1%}  junior accuracy={acc:.1%}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_prep = sub.add_parser("prepare")
    p_prep.add_argument("--n-choice", type=int, default=3500)
    p_prep.add_argument("--n-label", type=int, default=3500)
    p_run = sub.add_parser("run")
    p_run.add_argument("--juniors", nargs=2, default=list(DEFAULT_JUNIORS))
    p_run.add_argument("--ollama-url", default="http://localhost:11434")
    p_run.add_argument("--concurrency", type=int, default=2)
    p_run.add_argument(
        "--timeout-s", type=float, default=60.0, help="batch timeout (generous; see assemble)"
    )
    p_asm = sub.add_parser("assemble")
    p_asm.add_argument(
        "--junior-timeout-s",
        type=float,
        default=8.0,
        help="router junior timeout; slower answers count as unavailable (interface I3)",
    )
    args = ap.parse_args()
    if args.cmd == "prepare":
        prepare(args.n_choice, args.n_label)
    elif args.cmd == "run":
        asyncio.run(run(tuple(args.juniors), args.ollama_url, args.concurrency, args.timeout_s))
    else:
        assemble(args.junior_timeout_s)


if __name__ == "__main__":
    main()
