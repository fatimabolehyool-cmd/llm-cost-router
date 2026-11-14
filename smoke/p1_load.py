"""P1 load test on the mock stack (no Ollama, no API cost): what does the router itself add?

Both mock juniors answer after a fixed MODEL_S seconds, so overhead = router latency - MODEL_S.
It covers validation, the privacy check, the two parallel junior calls, the routing decision
(rule-v0 here; the MLServer hop was measured on its own in E2), the egress gateway with its
PostgreSQL spend reservation on escalations, and the asynchronous routing record.

Scenarios: the juniors agree (answered in-house) at 1, 2 and 4 concurrent requests, and disagree
(escalated to the mock senior) at 4, the junior cap. Targets: NFR-1 overhead p95 <= 200 ms and
NFR-5 success >= 98% at up to 4 concurrent requests.

    make p1        # starts the mock stack, runs this, writes results/p1_load_<stamp>.md/.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import httpx
from harness import RESULTS, add_target_args, target_from

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.metrics import percentile  # noqa: E402

SAMPLES = Path(__file__).resolve().parents[1] / "router" / "static" / "demo_samples.json"
SCENARIOS = [("agree", 1, 100), ("agree", 2, 100), ("agree", 4, 200), ("disagree", 4, 60)]
OVERHEAD_P95_MS = 200.0  # NFR-1
MIN_SUCCESS = 0.98  # NFR-5


def expected(kind: str, out: dict) -> bool:
    """The outcome each scenario must produce: in-house when they agree, Opus when they don't."""
    escalated = kind == "disagree"
    return out.get("status") == "answered" and bool(out.get("escalated")) == escalated


def summarize(kind: str, conc: int, results: list[dict], wall_s: float, model_ms: float) -> dict:
    ok = [r for r in results if r["code"] == 200 and r["out"] and expected(kind, r["out"])]
    router_ms = [float(r["out"]["latency_ms"]) for r in ok]
    overhead = [m - model_ms for m in router_ms]
    client_ms = [r["ms"] for r in results]
    return {
        "scenario": kind,
        "concurrency": conc,
        "n": len(results),
        "success": len(ok) / len(results) if results else 0.0,
        "throughput_rps": len(results) / wall_s if wall_s else 0.0,
        "client_p50_ms": percentile(client_ms, 50),
        "client_p95_ms": percentile(client_ms, 95),
        "router_p95_ms": percentile(router_ms, 95),
        "overhead_p50_ms": percentile(overhead, 50),
        "overhead_p95_ms": percentile(overhead, 95),
        "overhead_max_ms": max(overhead) if overhead else float("nan"),
        "codes": dict(Counter(r["code"] for r in results)),
    }


def passed(rows: list[dict]) -> bool:
    return all(
        r["overhead_p95_ms"] <= OVERHEAD_P95_MS and r["success"] >= MIN_SUCCESS for r in rows
    )


async def run(router: str, key: str, kind: str, conc: int, n: int, questions: list[dict]) -> list:
    sem = asyncio.Semaphore(conc)
    headers = {"X-API-Key": key}

    async def one(client: httpx.AsyncClient, q: dict) -> dict:
        async with sem:
            body = {"question": q["question"], "options": q["options"], "mode": "balanced"}
            t0 = time.perf_counter()
            try:
                r = await client.post(f"{router}/v1/answer", json=body, headers=headers)
                out = r.json() if r.status_code == 200 else None
                code = r.status_code
            except httpx.HTTPError:
                out, code = None, 0
            return {"code": code, "ms": (time.perf_counter() - t0) * 1000, "out": out}

    limits = httpx.Limits(max_connections=conc + 2)
    async with httpx.AsyncClient(timeout=30, limits=limits) as client:
        t0 = time.perf_counter()
        results = await asyncio.gather(
            *(one(client, questions[i % len(questions)]) for i in range(n))
        )
        return [results, time.perf_counter() - t0]


def render(rows: list[dict], model_s: float, stamp: str) -> str:
    lines = [
        f"# P1 load test ({stamp})",
        "",
        f"Mock stack (`make p1`): both mock juniors answer after {model_s:.1f} s, so overhead = "
        "router latency minus that model time. Policy: rule-v0 (the MLServer hop adds 1.9 ms p95, "
        "E2). Questions: the 20 exam questions in `router/static/demo_samples.json`, mode balanced.",
        "",
        "| Scenario | Concurrent | Requests | Success | Throughput | Client p50 / p95 | "
        "Overhead p50 / p95 / max | HTTP codes |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['scenario']} | {r['concurrency']} | {r['n']} | {r['success']:.1%} | "
            f"{r['throughput_rps']:.1f} req/s | {r['client_p50_ms']:.0f} / {r['client_p95_ms']:.0f} ms | "
            f"{r['overhead_p50_ms']:.0f} / {r['overhead_p95_ms']:.0f} / {r['overhead_max_ms']:.0f} ms | "
            f"{r['codes']} |"
        )
    verdict = "PASS" if passed(rows) else "FAIL"
    lines += [
        "",
        f"**{verdict}**: NFR-1 overhead p95 <= {OVERHEAD_P95_MS:.0f} ms and NFR-5 success "
        f">= {MIN_SUCCESS:.0%} in every scenario.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_target_args(ap)
    ap.add_argument("--model-s", type=float, default=0.5, help="mock model answer time, seconds")
    args = ap.parse_args()
    target = target_from(args)
    data = json.loads(SAMPLES.read_text(encoding="utf-8"))
    questions = [i for i in data["items"] if i["task_type"] == "choice"]
    slow = f"slow:{args.model_s}"
    rows = []
    with httpx.Client() as client:
        try:
            client.get(f"{target.router}/ready", timeout=5).raise_for_status()
        except httpx.HTTPError as exc:
            print(f"router not ready at {target.router} ({exc}); run `make p1`", file=sys.stderr)
            return 2
        try:
            for kind, conc, n in SCENARIOS:
                b = "C" if kind == "disagree" else "B"
                target.mock_control(
                    client,
                    **{"junior-a": f"{slow}:B", "junior-b": f"{slow}:{b}", "senior": "answer:B"},
                )
                asyncio.run(run(target.router, target.api_key, kind, 1, 3, questions))  # warm-up
                results, wall = asyncio.run(
                    run(target.router, target.api_key, kind, conc, n, questions)
                )
                rows.append(summarize(kind, conc, results, wall, args.model_s * 1000))
                print(
                    f"{kind:9} x{conc}: success {rows[-1]['success']:.1%}, "
                    f"overhead p95 {rows[-1]['overhead_p95_ms']:.0f} ms"
                )
        finally:
            target.mock_reset(client)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    md = render(rows, args.model_s, stamp)
    (RESULTS / f"p1_load_{stamp}.md").write_text(md, encoding="utf-8")
    (RESULTS / f"p1_load_{stamp}.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(md)
    return 0 if passed(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
