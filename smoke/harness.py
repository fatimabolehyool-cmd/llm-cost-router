"""Shared helpers for the feasibility checks. Works in two modes:
  compose mode (default on the Mac): DB queries and logs go through `docker compose`;
  native mode (--db-dsn / --log-file): used to test the checks themselves without Docker.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
COMPOSE = ["docker", "compose", "-f", str(ROOT / "docker-compose.yml")]


@dataclass
class Target:
    router: str = "http://localhost:8000"
    mock: str = "http://localhost:9100"
    api_key: str = "dev-key-change-me"
    db_dsn: str | None = None        # native mode
    log_file: str | None = None      # native mode

    # ---- database ----
    def sql(self, query: str) -> list[str]:
        if self.db_dsn:
            cmd = ["psql", self.db_dsn, "-Atc", query]
        else:
            cmd = COMPOSE + ["exec", "-T", "postgres", "psql", "-U", "router", "-d", "router", "-Atc", query]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            raise RuntimeError(f"sql failed: {out.stderr.strip()[:300]}")
        return [l for l in out.stdout.splitlines() if l]

    # ---- router logs ----
    def router_logs(self) -> str:
        if self.log_file:
            return Path(self.log_file).read_text(errors="replace")
        out = subprocess.run(COMPOSE + ["logs", "--no-color", "router"], capture_output=True, text=True, timeout=60)
        return out.stdout + out.stderr

    # ---- HTTP ----
    def ask(self, client: httpx.Client, **body) -> httpx.Response:
        return client.post(f"{self.router}/v1/answer", json=body, headers={"X-API-Key": self.api_key}, timeout=30)

    def mock_control(self, client: httpx.Client, **behaviour) -> None:
        client.post(f"{self.mock}/control", json=behaviour, timeout=5).raise_for_status()

    def mock_reset(self, client: httpx.Client) -> None:
        client.post(f"{self.mock}/control/reset", timeout=5).raise_for_status()

    def mock_records(self, client: httpx.Client, kind: str) -> list[str]:
        return client.get(f"{self.mock}/records/{kind}", timeout=5).json()["lines"]


def add_target_args(ap) -> None:
    ap.add_argument("--router", default="http://localhost:8000")
    ap.add_argument("--mock", default="http://localhost:9100")
    ap.add_argument("--api-key", default="dev-key-change-me")
    ap.add_argument("--db-dsn", default=None, help="native mode: psql connection string")
    ap.add_argument("--log-file", default=None, help="native mode: router log file")


def target_from(args) -> Target:
    return Target(args.router, args.mock, args.api_key, args.db_dsn, args.log_file)


CHOICE = dict(task_type="choice", options=["Mars", "Jupiter", "Venus", "Earth"])
LABEL = dict(task_type="label", options=["card_arrival", "lost_or_stolen_card", "refund_not_showing_up",
                                          "exchange_rate", "pin_blocked"])
