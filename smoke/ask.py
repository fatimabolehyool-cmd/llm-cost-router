"""Send a few real requests through the running router and print each routing record.

Used after `make real-up` (Ollama juniors, Claude senior, MLServer policy). Costs well under
$0.01: at most two senior calls. Shows the paths the demo needs: juniors accepted by the
policy, a hard ticket that may escalate, a sensitive ticket that must stay local, and
best-quality mode going straight to the senior.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
ROUTER = os.environ.get("ROUTER_URL", "http://localhost:8000")
KEY = os.environ.get("ROUTER_API_KEY", "dev-key-change-me")
LABELS = json.loads((ROOT / "data" / "banking77_labels.json").read_text(encoding="utf-8"))
PLANETS = ["Venus", "Mercury", "Earth", "Mars"]

REQUESTS = [
    (
        "easy choice",
        {
            "question": "Which planet is closest to the Sun?",
            "task_type": "choice",
            "options": PLANETS,
            "mode": "balanced",
        },
    ),
    (
        "help-desk ticket",
        {
            "question": "I was charged twice for the same coffee this morning.",
            "task_type": "label",
            "options": LABELS,
            "mode": "balanced",
        },
    ),
    (
        "sensitive ticket",
        {
            "question": "My card 4111 1111 1111 1111 still hasn't arrived after two weeks.",
            "task_type": "label",
            "options": LABELS,
            "mode": "balanced",
        },
    ),
    (
        "best quality",
        {
            "question": "Which planet is closest to the Sun?",
            "task_type": "choice",
            "options": PLANETS,
            "mode": "best_quality",
        },
    ),
]


def main() -> int:
    print(
        f"{'request':18s} {'status':10s} {'answer':28s} {'reason':26s} {'policy':18s} {'esc':4s} {'cost $':>8s} {'ms':>6s}"
    )
    failures = 0
    with httpx.Client(timeout=60) as client:
        for name, body in REQUESTS:
            r = client.post(f"{ROUTER}/v1/answer", json=body, headers={"X-API-Key": KEY})
            if r.status_code != 200:
                failures += 1
                print(f"{name:18s} HTTP {r.status_code}: {r.text[:120]}")
                continue
            o = r.json()
            print(
                f"{name:18s} {o['status']:10s} {str(o['answer'])[:28]:28s} {o['reason']:26s} "
                f"{o['versions']['policy'][:18]:18s} {'yes' if o['escalated'] else 'no':4s} "
                f"{o['cost_usd']:8.5f} {o['latency_ms']:6d}"
            )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
