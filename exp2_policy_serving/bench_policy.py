"""Experiment 2, step 2: measure the router -> policy-server network hop.

Question: does calling the policy over HTTP (V2 Inference Protocol) fit the proposal's
60 ms budget for the policy call, at p95, alone and with 20 concurrent callers (NFR-2)?

Runs on the host, like the router would in development. Uses one keep-alive HTTP client,
as the router will. Also checks the response contract (shape, name, probabilities in [0, 1]).

Usage: python exp2_policy_serving/bench_policy.py --url http://localhost:8090 --n 500
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common.policy_features import FEATURES, build  # noqa: E402

BUDGET_MS = 60.0  # proposal latency budget for the policy call


def pct(xs, p):
    s = sorted(xs)
    k = (len(s) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def random_request(rng: random.Random) -> dict:
    ok1, ok2 = rng.random() < 0.97, rng.random() < 0.97
    task = rng.choice(["choice", "label"])
    x = build(
        task,
        4 if task == "choice" else 77,
        ok1 and ok2 and rng.random() < 0.6,
        ok1,
        ok2,
        rng.randint(30, 800),
        rng.randint(1, 300),
        rng.randint(1, 300),
    )
    return {
        "id": f"bench-{rng.randrange(10**9)}",
        "inputs": [
            {"name": "features", "shape": [1, len(FEATURES)], "datatype": "FP32", "data": x}
        ],
        "outputs": [{"name": "predict_proba"}],
    }


def check_contract(body: dict) -> str | None:
    try:
        out = body["outputs"][0]
        assert out["name"] == "predict_proba", f"name {out['name']}"
        assert out["shape"] == [1, 2], f"shape {out['shape']}"
        assert all(0.0 <= v <= 1.0 for v in out["data"]), "probability outside [0,1]"
        return None
    except Exception as e:
        return f"contract violation: {e}"


async def call(client, url, payload) -> tuple[float, str | None]:
    t0 = time.perf_counter()
    try:
        r = await client.post(f"{url}/v2/models/routing-policy/infer", json=payload, timeout=1.0)
        ms = (time.perf_counter() - t0) * 1000
        if r.status_code != 200:
            return ms, f"HTTP {r.status_code}: {r.text[:200]}"
        return ms, check_contract(r.json())
    except Exception as e:
        return (time.perf_counter() - t0) * 1000, f"{type(e).__name__}: {e}"


async def run(url: str, n: int, conc: int) -> None:
    rng = random.Random(42)
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=conc + 5)) as client:
        ready = await client.get(f"{url}/v2/health/ready")
        meta = (await client.get(f"{url}/v2/models/routing-policy")).json()
        print("ready:", ready.status_code, "| model metadata:", json.dumps(meta))
        bad = await client.post(
            f"{url}/v2/models/routing-policy/infer",
            json={
                "inputs": [
                    {"name": "features", "shape": [1, 3], "datatype": "FP32", "data": [1, 0, 0]}
                ]
            },
        )
        bad_status = bad.status_code  # tells us how the server reports a contract violation
        first_ms, first_err = await call(client, url, random_request(rng))
        for _ in range(50):  # warm-up
            await call(client, url, random_request(rng))

        seq = [await call(client, url, random_request(rng)) for _ in range(n)]

        sem = asyncio.Semaphore(conc)

        async def one():
            async with sem:
                return await call(client, url, random_request(rng))

        t0 = time.perf_counter()
        par = await asyncio.gather(*(one() for _ in range(n)))
        par_s = time.perf_counter() - t0

    def row(name, res, extra=""):
        ok = [ms for ms, err in res if err is None]
        errs = [err for _, err in res if err]
        return (
            f"| {name} | {len(res)} | {100 * len(ok) / len(res):.1f}% | {pct(ok, 50):.1f} | {pct(ok, 95):.1f} | "
            f"{pct(ok, 99):.1f} | {max(ok):.1f} | {'PASS' if pct(ok, 95) <= BUDGET_MS else 'FAIL'} {extra}|",
            errs,
        )

    r1, e1 = row("sequential, 1 caller", seq)
    r2, e2 = row(f"stress: {conc} callers back-to-back", par, f"({n / par_s:.0f} req/s) ")
    inproc = ROOT / "results" / "exp2_inprocess_latency.json"
    ip = json.loads(inproc.read_text()) if inproc.exists() else {}
    lines = [
        f"# Experiment 2: policy serving hop ({datetime.now(UTC).isoformat(timespec='seconds')})",
        "",
        f"Target: {url}/v2/models/routing-policy/infer (V2 Inference Protocol, REST). "
        f"Budget for this hop: {BUDGET_MS:.0f} ms at p95. Client timeout 1 s. Measured, not estimated.",
        "",
        f"Model metadata: `{json.dumps(meta)}`",
        "",
        f"First call after warm start: {first_ms:.1f} ms ({first_err or 'ok'})",
        "",
        f"Malformed request (3 features instead of {len(FEATURES)}) returned HTTP {bad_status} "
        f"(a 5xx means the router must validate inputs itself and not trust the status code).",
        "",
        "| Scenario | Calls | Success | p50 ms | p95 ms | p99 ms | max ms | p95 within budget |",
        "|---|---|---|---|---|---|---|---|",
        r1,
        r2,
        "",
        f"In-process baseline (same model, no HTTP): p50 {ip.get('p50_ms', float('nan')):.3f} ms, "
        f"p95 {ip.get('p95_ms', float('nan')):.3f} ms.",
        "",
        f"Errors: {len(e1) + len(e2)}" + ("" if not (e1 + e2) else f" — first: {(e1 + e2)[0]}"),
    ]
    out = ROOT / "results" / f"exp2_summary_{datetime.now().strftime('%Y%m%d-%H%M')}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nsaved {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8090")
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--concurrency", type=int, default=20)
    a = ap.parse_args()
    asyncio.run(run(a.url, a.n, a.concurrency))


if __name__ == "__main__":
    main()
