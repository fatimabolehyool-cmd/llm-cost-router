"""S3 — Canary test: sensitive data cannot leave the system (professor's feedback 10).

Question: can a sensitive request reach the external (senior) provider, the database, or the
logs through ANY path — normal answers, disagreement, model failures, timeouts, a model that
echoes the user's text back, or any of the three modes?

Method:
  * Every sensitive request carries a unique canary token. Two ways to be sensitive are tested:
      flag  - the caller sets "sensitive": true (token inside the question)
      pii   - the caller does NOT flag it, but the text contains an email address built from the
              token, so the PII detector must catch it (fail-closed classification)
  * The full failure matrix is run: junior behaviours x senior behaviours x modes x task types.
  * Afterwards the token is searched for in: the mock senior's received payloads, every column of
    request_log, and the router's logs. It must appear in NONE of them.
  * Positive control: a NON-sensitive request with its own token is forced to escalate; that token
    MUST appear at the mock senior. If it does not, the test cannot see leaks and is invalid.
  * Sanity: the sensitive tokens MUST appear at the mock juniors (local models legitimately see the
    question), proving the requests really went through the pipeline.

Run with BREAKER_COOLDOWN_S=0 on the router (`make s3` does this), otherwise an open circuit
breaker turns later scenarios into 503s and they no longer test the path they claim to.

Usage (stack running):  python smoke/s3_canary.py            # compose mode
"""

from __future__ import annotations

import argparse
import itertools
import sys
import time
import uuid
from collections import Counter
from datetime import UTC, datetime

import httpx
from harness import CHOICE, LABEL, RESULTS, add_target_args, target_from

JUNIOR_PAIRS = {
    "agree": {"junior-a": "answer:A", "junior-b": "answer:A"},
    "disagree": {"junior-a": "answer:A", "junior-b": "answer:B"},
    "both_echo": {"junior-a": "echo", "junior-b": "echo"},  # models repeat the personal data
    "j1_timeout": {"junior-a": "timeout", "junior-b": "answer:B"},
    "j2_error": {"junior-a": "answer:A", "junior-b": "error"},
    "both_error": {"junior-a": "error", "junior-b": "error"},
    "unreadable": {"junior-a": "answer:no idea", "junior-b": "answer:maybe"},
}
SENIOR = {"ok": "answer:B", "error": "error", "echo": "echo"}
MODES = ["cheapest", "balanced", "best_quality"]


def label_answers(pair: dict) -> dict:
    """For label tasks, map A/B to real category names so 'agree' still means agree."""
    m = {"answer:A": "answer:card_arrival", "answer:B": "answer:pin_blocked"}
    return {k: m.get(v, v) for k, v in pair.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    add_target_args(ap)
    t = target_from(ap.parse_args())
    run_id = uuid.uuid4().hex[:8]
    problems: list[str] = []
    outcomes: Counter = Counter()
    tokens: list[str] = []
    n = 0
    t0 = time.time()

    def positive_control(c: httpx.Client, tag: str) -> str:
        tok = f"POSCTRL{run_id}{tag}"
        t.mock_control(c, **JUNIOR_PAIRS["disagree"], senior="answer:B")
        r = t.ask(c, question=f"Plain question {tok} about planets?", mode="balanced", **CHOICE)
        return tok if r.status_code == 200 and r.json().get("escalated") else ""

    with httpx.Client() as c:
        t.mock_reset(c)
        pos_first = positive_control(c, "first")
        for (jname, jpair), (sname, sbeh), mode, task, how in itertools.product(
            JUNIOR_PAIRS.items(), SENIOR.items(), MODES, [CHOICE, LABEL], ["flag", "pii"]
        ):
            pair = label_answers(jpair) if task is LABEL else jpair
            senior = "answer:pin_blocked" if (task is LABEL and sname == "ok") else sbeh
            t.mock_control(c, **pair, senior=senior)
            token = f"CANARY{run_id}x{n:04d}"
            tokens.append(token)
            if how == "flag":
                body = dict(
                    question=f"Customer {token} asks which one is largest?",
                    sensitive=True,
                    mode=mode,
                    **task,
                )
            else:
                body = dict(
                    question=f"Reply to {token.lower()}@example.com about their card",
                    sensitive=False,
                    mode=mode,
                    **task,
                )
            r = t.ask(c, **body)
            n += 1
            if r.status_code == 200:
                j = r.json()
                outcomes[(j["status"], j["reason"])] += 1
                if j["data_class"] != "sensitive":
                    problems.append(f"{token}: classified {j['data_class']} ({how})")
                if j["escalated"] or (j["model"] or "").startswith("senior"):
                    problems.append(f"{token}: escalated to senior ({jname}/{sname}/{mode})")
            elif r.status_code == 503:
                outcomes[("503", r.json()["error"]["code"])] += 1
            else:
                problems.append(f"{token}: unexpected HTTP {r.status_code}")

        pos_last = positive_control(c, "last")
        time.sleep(2)  # let the async logger flush
        senior_seen = "\n".join(t.mock_records(c, "senior"))
        junior_seen = "\n".join(t.mock_records(c, "juniors"))

    leaked_senior = [tk for tk in tokens if tk in senior_seen or tk.lower() in senior_seen]
    db_dump = "\n".join(t.sql("SELECT row_to_json(r)::text FROM request_log r"))
    leaked_db = [tk for tk in tokens if tk in db_dump or tk.lower() in db_dump]
    logs = t.router_logs()
    leaked_logs = [tk for tk in tokens if tk in logs or tk.lower() in logs]
    juniors_saw = sum(1 for tk in tokens if tk in junior_seen or tk.lower() in junior_seen)
    pos_ok = bool(pos_first and pos_last and pos_first in senior_seen and pos_last in senior_seen)

    checks = [
        ("Sensitive tokens found at the external (senior) provider", len(leaked_senior), "0"),
        ("Sensitive tokens found in request_log (any column)", len(leaked_db), "0"),
        ("Sensitive tokens found in router logs", len(leaked_logs), "0"),
        ("Sensitive requests misclassified or escalated", len(problems), "0"),
        (
            "Positive controls (before and after the matrix) reached the senior",
            "yes" if pos_ok else "NO",
            "yes",
        ),
        (
            "Sensitive requests that reached the local juniors (sanity)",
            f"{juniors_saw}/{len(tokens)}",
            "> 0",
        ),
    ]
    passed = (
        not (leaked_senior or leaked_db or leaked_logs or problems) and pos_ok and juniors_saw > 0
    )

    lines = [
        f"# S3 canary test ({datetime.now(UTC).isoformat(timespec='seconds')})",
        "",
        f"**Result: {'PASS' if passed else 'FAIL'}** — {len(tokens)} sensitive requests "
        f"({len(JUNIOR_PAIRS)} junior behaviours x {len(SENIOR)} senior behaviours x {len(MODES)} modes "
        f"x 2 task types x 2 ways of being sensitive), run time {time.time() - t0:.0f} s. Measured.",
        "",
        "| Check | Observed | Required |",
        "|---|---|---|",
        *[f"| {a} | {b} | {c_} |" for a, b, c_ in checks],
        "",
        "## Outcomes of the sensitive requests",
        "",
        "| Status | Reason | Count |",
        "|---|---|---|",
        *[f"| {s} | {r} | {k} |" for (s, r), k in sorted(outcomes.items())],
        "",
        "Limitations: mock models, not real ones; the PII detector covers emails, UAE phone numbers, "
        "Emirates IDs and card numbers (Luhn) only, so other personal data in an unflagged request is a "
        "stated residual risk.",
    ]
    if problems:
        lines += ["", "## Problems", "", *[f"- {p}" for p in problems[:50]]]
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / "s3_canary_summary.md"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nsaved {out}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
