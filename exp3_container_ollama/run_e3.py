"""Experiment 3 (architecture feedback A3): does the router container -> Ollama hop add latency?

E1 measured the juniors from the Mac host. In the deployed system every junior call goes from
the router container to Ollama on the host through `host.docker.internal` (interface I3).
This measures both paths with the same requests, so the difference is the cost of that hop.

  measure    --url URL --label host|container   one series; JSON to stdout or --out
             * ping: GET /api/version, sequential (network hop only, no model work)
             * chat: POST /api/chat with a fixed junior-style request (hop + model time)
  summarize  RAW... --out FILE                  Markdown table comparing the series

Self-contained (httpx + standard library), so it runs inside the existing router image
without a rebuild: the Makefile mounts this folder read-only into the container.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from datetime import UTC, datetime

import httpx

SYSTEM = (
    "You answer multiple-choice questions. Reply with only the letter of the correct option "
    "(A, B, C or D). Do not explain."
)
USER = "Which planet is closest to the Sun?\n\nA. Venus\nB. Mercury\nC. Earth\nD. Mars\n\nAnswer:"


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    k = (len(xs) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def measure(url: str, label: str, model: str, n_ping: int, n_chat: int) -> dict:
    url = url.rstrip("/")
    body = {
        "model": model,
        "stream": False,
        "keep_alive": "30m",
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER}],
        "options": {"temperature": 0, "seed": 42, "num_predict": 32, "num_ctx": 4096},
        "think": False,
    }
    ping_ms, chat_ms, errors = [], [], []
    with httpx.Client(timeout=60) as client:
        try:
            r = client.get(f"{url}/api/version")
            r.raise_for_status()
            version = r.json().get("version")
            client.post(f"{url}/api/chat", json=body).raise_for_status()  # warm-up: model loaded
        except httpx.HTTPError as e:
            return {"label": label, "url": url, "reachable": False, "error": type(e).__name__}
        for _ in range(n_ping):
            t0 = time.perf_counter()
            try:
                client.get(f"{url}/api/version").raise_for_status()
                ping_ms.append((time.perf_counter() - t0) * 1000)
            except httpx.HTTPError as e:
                errors.append(f"ping:{type(e).__name__}")
        for _ in range(n_chat):
            t0 = time.perf_counter()
            try:
                client.post(f"{url}/api/chat", json=body).raise_for_status()
                chat_ms.append((time.perf_counter() - t0) * 1000)
            except httpx.HTTPError as e:
                errors.append(f"chat:{type(e).__name__}")
    return {
        "label": label,
        "url": url,
        "reachable": True,
        "ollama_version": version,
        "model": model,
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "client_platform": platform.platform(),
        "ping_ms": [round(x, 2) for x in ping_ms],
        "chat_ms": [round(x, 1) for x in chat_ms],
        "errors": errors,
    }


def stats(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "p50": pct(xs, 50), "p95": pct(xs, 95), "mean": statistics.fmean(xs)}


def fmt(st: dict, q: str, digits: int) -> str:
    return f"{st[q]:.{digits}f}" if st["n"] else "–"


def load_series(path: str) -> dict:
    """Read one raw series; an empty or broken file means that measurement never ran."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        label = path.rsplit("e3_raw_", 1)[-1].rsplit("_", 2)[0]
        return {
            "label": label,
            "reachable": False,
            "error": "no output: the measurement did not run",
        }


def summarize(paths: list[str], out: str) -> None:
    series = [load_series(p) for p in paths]
    lines = [
        "# Experiment 3: router container → Ollama vs host → Ollama",
        "",
        "All figures measured on this Mac; none are estimates. Same request in every series;",
        "`ping` = `GET /api/version` (network hop only); `chat` = one junior-style call "
        "(hop + model time).",
        "",
        "| Series | Reachable | Ping p50 ms | Ping p95 ms | Chat p50 ms | Chat p95 ms | Errors |",
        "|---|---|---|---|---|---|---|",
    ]
    by_label: dict[str, list[dict]] = {}
    for s in series:
        if not s.get("reachable"):
            lines.append(f"| {s['label']} | **no** ({s.get('error')}) | | | | | |")
            continue
        p, c = stats(s["ping_ms"]), stats(s["chat_ms"])
        if p["n"] and c["n"]:
            by_label.setdefault(s["label"].split("_")[0], []).append({"ping": p, "chat": c})
        lines.append(
            f"| {s['label']} | yes | {fmt(p, 'p50', 2)} | {fmt(p, 'p95', 2)} | "
            f"{fmt(c, 'p50', 0)} | {fmt(c, 'p95', 0)} | {len(s['errors'])} |"
        )
    if "host" in by_label and "container" in by_label:

        def avg(label: str, kind: str, q: str) -> float:
            return statistics.fmean(x[kind][q] for x in by_label[label])

        lines += [
            "",
            "**Overhead of the container path** (container minus the mean of the host series):",
            "",
        ]
        for kind in ("ping", "chat"):
            for q in ("p50", "p95"):
                d = avg("container", kind, q) - avg("host", kind, q)
                lines.append(f"- {kind} {q}: {d:+.2f} ms")
        lines += [
            "",
            "Context: the junior timeout is 8,000 ms (I3) and the routing-overhead budget is "
            "200 ms p95 (NFR). Two host series bracket the container series, so a large gap "
            "between them would indicate thermal drift rather than a network effect.",
        ]
    md = "\n".join(lines) + "\n"
    open(out, "w", encoding="utf-8").write(md)
    print(md)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("measure")
    m.add_argument("--url", required=True)
    m.add_argument("--label", required=True)
    m.add_argument("--model", default="gemma3:12b")
    m.add_argument("--n-ping", type=int, default=200)
    m.add_argument("--n-chat", type=int, default=30)
    m.add_argument("--out", default="-")
    s = sub.add_parser("summarize")
    s.add_argument("raw", nargs="+")
    s.add_argument("--out", required=True)
    args = ap.parse_args()
    if args.cmd == "summarize":
        summarize(args.raw, args.out)
        return 0
    result = measure(args.url, args.label, args.model, args.n_ping, args.n_chat)
    text = json.dumps(result, indent=1)
    if args.out == "-":
        sys.stdout.write(text + "\n")
    else:
        open(args.out, "w", encoding="utf-8").write(text + "\n")
    if not result.get("reachable"):
        print(
            f"E3: Ollama not reachable at {args.url} ({result.get('error')}). "
            "Is `ollama serve` running? From a container, is Docker Desktop running?",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
