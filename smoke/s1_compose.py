"""S1 — Minimum deployable system: does the core stack start, serve, fail safely and recover?

Question: on this Mac, does router + PostgreSQL (+ mock models) start from a clean checkout in
the right order, serve all three modes, enforce auth and the overload cap, fail safely when the
database is down, recover on its own, and how much memory does it use?

Steps (all measured; written to results/s1_compose_summary.md):
  1. `docker compose --profile mocks up -d --build`, time until the router reports healthy
  2. one request per mode + one forced escalation; check every one was logged
  3. 401 without a key; 400 for a bad body
  4. overload: 12 concurrent slow requests against a cap of 4 -> some 503 "overloaded",
     and the senior is never called because of overload
  5. stop PostgreSQL -> /ready and /v1/answer return 503; start it -> time until ready again
  6. `docker stats` memory per container; image digests for docs/versions.md
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import subprocess
import sys
import time
from datetime import datetime, timezone

import httpx

from harness import CHOICE, COMPOSE, RESULTS, add_target_args, target_from


def sh(*args: str, timeout: int = 900) -> str:
    out = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed:\n{out.stderr[-2000:]}")
    return out.stdout


def wait_ready(url: str, limit_s: float = 180) -> float:
    t0 = time.time()
    while time.time() - t0 < limit_s:
        try:
            if httpx.get(f"{url}/ready", timeout=2).status_code == 200:
                return time.time() - t0
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise TimeoutError("router did not become ready")


def main() -> int:
    ap = argparse.ArgumentParser()
    add_target_args(ap)
    ap.add_argument("--native", action="store_true", help="skip docker steps (testing the script itself)")
    args = ap.parse_args()
    t = target_from(args)
    rows: list[tuple[str, str, str, bool]] = []   # check, observed, required, pass

    def check(name, observed, required, ok):
        rows.append((name, str(observed), required, bool(ok)))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {observed}")

    # 1. start
    if not args.native:
        t0 = time.time()
        sh(*COMPOSE, "--profile", "mocks", "up", "-d", "--build")
        build_up = time.time() - t0
        ready_s = wait_ready(t.router)
        check("Clean start: build + start + healthy", f"{build_up + ready_s:.0f} s", "< 600 s", build_up + ready_s < 600)
        sh(*COMPOSE, "--profile", "mocks", "restart", "router")
        warm = wait_ready(t.router)
        check("Router restart until ready (warm)", f"{warm:.1f} s", "< 30 s", warm < 30)

    with httpx.Client() as c:
        t.mock_reset(c)
        before = int(t.sql("SELECT count(*) FROM request_log")[0])
        # 2. each mode
        statuses = {}
        for mode in ["cheapest", "balanced", "best_quality"]:
            r = t.ask(c, question="Which planet is the largest?", mode=mode, **CHOICE)
            statuses[mode] = (r.status_code, r.json().get("reason"))
        t.mock_control(c, **{"junior-a": "answer:A", "junior-b": "answer:B", "senior": "answer:B"})
        r = t.ask(c, question="Which planet is the largest?", mode="balanced", **CHOICE)
        statuses["balanced_disagree"] = (r.status_code, r.json().get("reason"))
        check("All modes answer (HTTP, reason)", json.dumps(statuses), "all 200",
              all(s == 200 for s, _ in statuses.values()) and statuses["balanced_disagree"][1] == "escalated_disagreement")
        time.sleep(2)
        logged = int(t.sql("SELECT count(*) FROM request_log")[0]) - before
        check("Requests written to request_log", logged, "4", logged == 4)

        # 3. auth and validation
        r401 = c.post(f"{t.router}/v1/answer", json={}, timeout=10).status_code
        r400 = t.ask(c, question="x", task_type="choice", options=["a", "b"]).status_code
        check("No API key / invalid body", f"{r401} / {r400}", "401 / 400", (r401, r400) == (401, 400))

        # 4. overload
        t.mock_reset(c)
        t.mock_control(c, **{"junior-a": "slow:1:A", "junior-b": "slow:1:B", "senior": "answer:A"})
        with cf.ThreadPoolExecutor(12) as pool:
            res = list(pool.map(lambda _: t.ask(httpx.Client(), question="Largest planet?", mode="balanced",
                                                **CHOICE), range(12)))
        codes = [r.status_code for r in res]
        overloaded = sum(1 for r in res if r.status_code == 503 and r.json()["error"]["code"] == "overloaded")
        senior_calls = len(t.mock_records(c, "senior"))
        served = codes.count(200)
        check("Overload: 12 concurrent slow requests, cap 4", f"{served} served, {overloaded} rejected 503",
              "4 served, 8 rejected", served == 4 and overloaded == 8)
        check("Senior calls = served escalations only (no overload escalations)", f"{senior_calls} vs {served}",
              "equal", senior_calls == served)
        t.mock_reset(c)

        # 5. database outage
        if not args.native:
            sh(*COMPOSE, "stop", "postgres")
            ready_code = httpx.get(f"{t.router}/ready", timeout=5).status_code
            ans = t.ask(c, question="Largest planet?", mode="balanced", **CHOICE)
            check("PostgreSQL down: /ready and /v1/answer", f"{ready_code} / {ans.status_code}", "503 / 503",
                  (ready_code, ans.status_code) == (503, 503))
            t0 = time.time()
            sh(*COMPOSE, "start", "postgres")
            rec = wait_ready(t.router)
            check("Recovery after PostgreSQL restarts (no router restart)", f"{rec:.1f} s", "< 60 s", rec < 60)

    # 6. resources and versions
    stats = images = ""
    if not args.native:
        stats = sh("docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}")
        images = sh(*COMPOSE, "--profile", "mocks", "images", "--format", "json")

    passed = all(ok for *_, ok in rows)
    lines = [f"# S1 minimum deployable system ({datetime.now(timezone.utc).isoformat(timespec='seconds')})", "",
             f"**Result: {'PASS' if passed else 'FAIL'}.** All values measured on this machine.", "",
             "| Check | Observed | Required | Result |", "|---|---|---|---|",
             *[f"| {a} | {b} | {c_} | {'pass' if ok else 'FAIL'} |" for a, b, c_, ok in rows]]
    if stats:
        lines += ["", "## Memory and CPU (docker stats, idle after the checks)", "", "```", stats.strip(), "```"]
    if images:
        lines += ["", "## Images (for docs/versions.md)", "", "```", images.strip()[:4000], "```"]
    lines += ["", "Limitations: mock models (no model latency); one request at a time except the overload check."]
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / "s1_compose_summary.md"
    out.write_text("\n".join(lines) + "\n")
    print(f"\nsaved {out}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
