"""Lifecycle demo (plan item M8, interface I7) on the running real stack, in one command.

    make lifecycle-demo        (after `make real-up`; Ollama, Docker and the admin key needed)

1. Before: which policy serves, and one real request decided by it.
2. Gate refusal: try to promote a registered version that failed the release gate (refused).
3. Train: the Airflow DAG re-trains the policy, gates it against rule-v0 and writes the offline
   shadow report against the champion that serves now.
4. Promote the new challenger: the registry exports it, MLServer loads it and the router
   switches to it, all without a restart (the router container's start time is checked).
5. Roll back: the previous champion serves again, again without a restart.

Every step is checked; the results go to results/lifecycle_demo_<timestamp>.md. Cost: three
balanced requests (normally answered by the juniors, so usually $0).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
ROUTER = "http://localhost:8000"
COMPOSE = ["docker", "compose", "--profile", "orchestration"]
REGISTRY = COMPOSE + ["exec", "-T", "-w", "/opt/project", "airflow"]
REGISTRY += ["/opt/pipelines-venv/bin/python", "-m", "pipelines.registry"]
QUESTION = {
    "question": "Which planet is closest to the Sun?",
    "task_type": "choice",
    "options": ["Venus", "Mercury", "Earth", "Mars"],
    "mode": "balanced",
}


class Traffic:
    """Steady requests in the background while a switch happens: none may fail."""

    def __init__(self, headers: dict) -> None:
        self.headers, self.stop = headers, threading.Event()
        self.decided: Counter[str] = Counter()
        self.failed = 0
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        with httpx.Client(base_url=ROUTER, timeout=60) as c:
            while not self.stop.is_set():
                try:
                    r = c.post("/v1/answer", json=QUESTION, headers=self.headers)
                    r.raise_for_status()
                    self.decided[r.json()["versions"]["policy"]] += 1
                except httpx.HTTPError:
                    self.failed += 1

    def __enter__(self) -> Traffic:
        self.thread.start()
        time.sleep(1.0)  # at least one request before the switch starts
        return self

    def __exit__(self, *exc) -> None:
        time.sleep(1.0)
        self.stop.set()
        self.thread.join()

    def summary(self) -> str:
        n = sum(self.decided.values()) + self.failed
        by = ", ".join(f"{k} x{v}" for k, v in self.decided.items())
        return f"{n} requests during the switch, {self.failed} failed (decided by: {by})"


def dotenv() -> dict[str, str]:
    env = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


class Demo:
    def __init__(self, features: str) -> None:
        env = dotenv()
        self.features = features
        self.admin = {"X-Admin-Key": env.get("ROUTER_ADMIN_KEY", "")}
        self.caller = {"X-API-Key": os.environ.get("ROUTER_API_KEY", "dev-key-change-me")}
        self.http = httpx.Client(base_url=ROUTER, timeout=60)
        self.rows: list[tuple[str, str, str]] = []  # (step, what happened, PASS/FAIL/INFO)
        self.failed = False

    # ---------------------------------------------------------------- helpers
    def record(self, step: str, what: str, ok: bool | None = None) -> None:
        status = "INFO" if ok is None else ("PASS" if ok else "FAIL")
        self.failed |= ok is False
        self.rows.append((step, what, status))
        print(f"[{status}] {step}: {what}", flush=True)

    def sh(self, cmd: list[str]) -> tuple[int, str]:
        print("$ " + " ".join(cmd[-6:]), flush=True)
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        out = p.stdout + p.stderr
        print("\n".join(out.strip().splitlines()[-12:]), flush=True)
        return p.returncode, p.stdout

    def serving(self) -> str:
        r = self.http.get("/admin/policy", headers=self.admin)
        r.raise_for_status()
        return r.json()["policy"]

    def ask(self) -> dict:
        r = self.http.post("/v1/answer", json=QUESTION, headers=self.caller)
        r.raise_for_status()
        return r.json()

    def router_started_at(self) -> str:
        cid = subprocess.run(
            ["docker", "compose", "ps", "-q", "router"], cwd=ROOT, capture_output=True, text=True
        ).stdout.strip()
        return subprocess.run(
            ["docker", "inspect", "-f", "{{.State.StartedAt}}", cid],
            capture_output=True,
            text=True,
        ).stdout.strip()

    def registry(self) -> dict:
        rc, out = self.sh(REGISTRY + ["show", "--json"])
        if rc != 0:
            raise SystemExit("cannot read the MLflow registry (is the Airflow container up?)")
        return json.loads(out[out.index("{") :])

    def request_check(self, step: str, expected: str) -> None:
        out = self.ask()
        decided = out["versions"]["policy"]
        self.record(
            step,
            f"request answered ({out['reason']}, ${out['cost_usd']:.4f}); decided by {decided}",
            decided == expected,
        )

    # ---------------------------------------------------------------- steps
    def preflight(self) -> None:
        if not self.admin["X-Admin-Key"]:
            raise SystemExit(
                "no ROUTER_ADMIN_KEY in .env: run `make admin-key`, then `make real-up`"
            )
        try:
            r = self.http.get("/admin/policy", headers=self.admin)
        except httpx.TransportError:  # refused, or Docker's port open while the router restarts
            raise SystemExit(
                "the router is not answering: run `make real-up` and check it ends without errors"
            ) from None
        if r.status_code in (401, 403):
            raise SystemExit("the router has an older admin key: run `make real-up` to apply it")
        r.raise_for_status()

    def run(self) -> int:
        self.preflight()
        before, started = self.serving(), self.router_started_at()
        self.record("1 before", f"serving {before}; router container started {started}")
        self.request_check("1 before", before)

        rc, _ = self.sh(COMPOSE + ["up", "-d", "--wait", "airflow"])
        if rc != 0:
            raise SystemExit("Airflow did not start")
        failed_gate = [v for v in self.registry()["versions"] if not v["gate_passed"]]
        if failed_gate:
            v = failed_gate[-1]
            rc, _ = self.sh(REGISTRY + ["promote", "--version", v["version"]])
            self.record(
                "2 gate",
                f"promoting version {v['version']} ({v['feature_version']}, failed the gate) "
                f"was {'refused' if rc == 2 else 'NOT refused'}; still serving {self.serving()}",
                rc == 2 and self.serving() == before,
            )
        else:
            self.record("2 gate", "no gate-failed version in the registry to try")

        t = time.monotonic()
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True
        ).stdout.strip()
        rc, _ = self.sh(
            COMPOSE
            + ["exec", "-T", "-e", f"GIT_COMMIT={commit}", "airflow", "airflow", "dags", "test"]
            + ["revalidate_policy", stamp, "-c", json.dumps({"features": self.features})]
        )
        if rc != 0:
            self.record("3 train", "the Airflow DAG run failed (see the output above)", False)
            return self.finish()
        reg = self.registry()
        chal = reg["aliases"]["challenger"]
        info = next(v for v in reg["versions"] if v["version"] == chal)
        shadows = sorted((ROOT / "results").glob(f"shadow_*_vs_{info['model_version']}.md"))
        verdict = (
            re.search(r"\*\*Verdict: (.+?)\.\*\*", shadows[-1].read_text()) if shadows else None
        )
        self.record(
            "3 train",
            f"DAG run in {time.monotonic() - t:.0f} s: challenger = version {chal} "
            f"({info['model_version']}, gate {'passed' if info['gate_passed'] else 'failed'}); "
            f"shadow report: {verdict.group(1) if verdict else 'missing'}",
            rc == 0 and bool(shadows),
        )
        if not info["gate_passed"]:
            self.record("4 promote", "the new challenger failed the gate: nothing to promote")
            return self.finish()

        self.switch("4 promote", ["promote", "--version", chal], info["model_version"], started)
        self.switch("5 rollback", ["rollback"], before, started)
        return self.finish()

    def switch(self, step: str, command: list[str], expected: str, started: str) -> None:
        """Run a registry command under live traffic and check the result."""
        t = time.monotonic()
        with Traffic(self.caller) as traffic:
            rc, _ = self.sh(REGISTRY + command)
        took, now = time.monotonic() - t, self.serving()
        same_process = self.router_started_at() == started
        self.record(
            step,
            f"`{' '.join(command)}` took {took:.1f} s; now serving {now}; "
            f"router restarted: {'no' if same_process else 'YES'}",
            rc == 0 and now == expected and same_process,
        )
        self.record(step, traffic.summary(), traffic.failed == 0)
        self.request_check(step, expected)

    def finish(self) -> int:
        ts = datetime.now().strftime("%Y%m%d-%H%M")
        verdict = "FAIL" if self.failed else "PASS"
        lines = [
            f"# Lifecycle demo ({datetime.now().astimezone().isoformat(timespec='seconds')})",
            "",
            f"**Result: {verdict}.** Train, gate, shadow report, promote and roll back on the "
            "running stack; the router and MLServer switch models without a restart (I7).",
            "",
            "| Step | What happened | Check |",
            "|---|---|---|",
            *[f"| {s} | {w} | {c} |" for s, w, c in self.rows],
            "",
        ]
        out = ROOT / "results" / f"lifecycle_demo_{ts}.md"
        out.write_text("\n".join(lines))
        print(f"\n{verdict}: written {out.relative_to(ROOT)}")
        return 1 if self.failed else 0


if __name__ == "__main__":
    features = sys.argv[1] if len(sys.argv) > 1 else "v2"
    sys.exit(Demo(features).run())
