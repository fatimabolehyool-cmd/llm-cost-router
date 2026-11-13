"""End-to-end workflows on the Compose stack: router + PostgreSQL + the MOCK model server.

The mock server plays Ollama and the senior provider and records every payload it receives,
so these tests can check what did (and did not) leave the router. No Ollama, no API cost.
Run with `make e2e` (starts the stack with AUDIT_RATE=0 and BREAKER_COOLDOWN_S=0).
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
ROUTER = os.environ.get("E2E_ROUTER_URL", "http://localhost:8000")
MOCK = os.environ.get("E2E_MOCK_URL", "http://localhost:9100")
KEY = os.environ.get("ROUTER_API_KEY", "dev-key-change-me")
CARD = "4111 1111 1111 1111"
PLANETS = ["Venus", "Mercury", "Earth", "Mars"]


@pytest.fixture
def http():
    with httpx.Client(timeout=30) as c:
        c.post(f"{MOCK}/control/reset").raise_for_status()
        yield c
        c.post(f"{MOCK}/control/reset")


def behave(http, **models):
    http.post(f"{MOCK}/control", json=models).raise_for_status()


def ask(http, question="Which planet is closest to the Sun?", key=KEY, **over):
    body = {"question": question, "task_type": "choice", "options": PLANETS} | over
    return http.post(f"{ROUTER}/v1/answer", json=body, headers={"X-API-Key": key})


def senior_calls(http) -> list[str]:
    return http.get(f"{MOCK}/records/senior").json()["lines"]


def sql(query: str) -> list[str]:
    cmd = ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "router", "-d", "router"]
    out = subprocess.run(cmd + ["-Atc", query], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return [line for line in out.stdout.splitlines() if line]


def logged(request_id: str, columns: str, wait_s: float = 3.0) -> list[str]:
    """The routing record, once the asynchronous logger has written it."""
    deadline = time.monotonic() + wait_s
    while True:
        rows = sql(f"SELECT {columns} FROM request_log WHERE request_id = '{request_id}'")
        if rows or time.monotonic() > deadline:
            return rows
        time.sleep(0.2)


def spend() -> float:
    return float(sql("SELECT spent_usd FROM spend WHERE id = 1")[0])


# ---------------------------------------------------------------- the main workflows


def test_stack_is_up_and_reports_its_versions(http):
    assert http.get(f"{ROUTER}/health").json() == {"status": "ok"}
    assert http.get(f"{ROUTER}/ready").json() == {"status": "ready"}
    metrics = http.get(f"{ROUTER}/metrics").text
    assert "router_requests_total" in metrics and "router_policy_info" in metrics
    assert http.get(f"{ROUTER}/demo").status_code == 200


def test_agreement_is_answered_locally_logged_and_free(http):
    behave(http, **{"junior-a": "answer:B", "junior-b": "answer:B"})
    before = spend()
    r = ask(http)
    out = r.json()
    assert r.status_code == 200 and out["status"] == "answered" and out["answer"] == "B"
    assert (out["escalated"], out["cost_usd"], out["reason"]) == (False, 0.0, "juniors_agreed")
    assert senior_calls(http) == []
    assert logged(out["request_id"], "status, reason, escalated") == ["answered|juniors_agreed|f"]
    assert spend() == before


def test_disagreement_is_escalated_billed_and_logged(http):
    behave(http, **{"junior-a": "answer:A", "junior-b": "answer:D", "senior": "answer:B"})
    before = spend()
    out = ask(http).json()
    assert (out["status"], out["answer"], out["escalated"]) == ("answered", "B", True)
    assert out["reason"] == "escalated_disagreement" and out["cost_usd"] > 0
    assert len(senior_calls(http)) == 1
    row = logged(out["request_id"], "reason, final_model, cost_usd > 0")
    assert row == [f"escalated_disagreement|{out['model']}|t"]
    assert spend() > before


def test_every_mode_takes_its_path(http):
    behave(http, **{"junior-a": "answer:A", "junior-b": "answer:C", "senior": "answer:B"})
    cheap = ask(http, mode="cheapest").json()
    assert (cheap["answer"], cheap["verified"], cheap["escalated"]) == ("A", False, False)
    best = ask(http, mode="best_quality").json()
    assert (best["answer"], best["reason"], best["escalated"]) == ("B", "best_quality_mode", True)
    assert len(senior_calls(http)) == 1  # only best_quality paid


def test_feedback_round_trip(http):
    out = ask(http).json()
    assert logged(out["request_id"], "request_id")  # the record exists
    headers = {"X-API-Key": KEY}
    body = {"request_id": out["request_id"], "flag": "wrong", "note": f"card {CARD} was wrong"}
    r = http.post(f"{ROUTER}/v1/feedback", json=body, headers=headers)
    assert r.status_code == 200 and r.json() == {"received": True}
    stored = sql(f"SELECT flag, note_masked FROM feedback WHERE request_id = '{out['request_id']}'")
    assert len(stored) == 1 and stored[0].startswith("wrong|") and CARD not in stored[0]
    unknown = body | {"request_id": "req_000000000000"}
    assert http.post(f"{ROUTER}/v1/feedback", json=unknown, headers=headers).status_code == 404


# ---------------------------------------------------------------- privacy, end to end


def test_sensitive_request_never_leaves_and_is_never_stored_in_clear(http):
    behave(http, **{"junior-a": "answer:A", "junior-b": "answer:C", "senior": "answer:B"})
    q = f"My card {CARD} was charged twice. Which planet is closest to the Sun?"
    out = ask(http, question=q).json()  # not flagged: the router must detect it itself
    assert (out["status"], out["data_class"]) == ("abstained", "sensitive")
    assert out["reason"] == "sensitive_disagreement" and out["cost_usd"] == 0
    assert senior_calls(http) == []  # nothing reached the external provider
    row = logged(out["request_id"], "data_class, prompt_masked")
    assert row and row[0].startswith("sensitive|") and CARD not in row[0]
    logs = subprocess.run(
        ["docker", "compose", "logs", "--no-color", "router"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout
    assert CARD not in logs and CARD.replace(" ", "") not in logs


# ---------------------------------------------------------------- failure cases


def test_both_juniors_down_is_a_503_not_a_paid_guess(http):
    behave(http, **{"junior-a": "error", "junior-b": "error"})
    r = ask(http)
    assert r.status_code == 503 and r.json()["error"]["code"] == "models_unavailable"
    assert senior_calls(http) == []


def test_one_junior_down_still_answers_through_the_senior(http):
    behave(http, **{"junior-a": "error", "junior-b": "answer:A", "senior": "answer:B"})
    out = ask(http).json()
    assert (out["status"], out["answer"], out["escalated"]) == ("answered", "B", True)


def test_senior_down_abstains_instead_of_guessing(http):
    behave(http, **{"junior-a": "answer:A", "junior-b": "answer:C", "senior": "error"})
    out = ask(http).json()
    assert (out["status"], out["answer"], out["reason"]) == (
        "abstained",
        None,
        "senior_unavailable",
    )


def test_bad_requests_are_rejected_before_any_model_is_called(http):
    assert ask(http, key="wrong").status_code == 401
    bad = ask(http, options=["Venus", "Venus", "Earth", "Mars"])
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_request"
    assert ask(http, question="").status_code == 400
    assert http.get(f"{MOCK}/records/juniors").json()["lines"] == []


def test_admin_endpoints_refuse_the_caller_key(http):
    r = http.get(f"{ROUTER}/admin/policy", headers={"X-Admin-Key": KEY})
    assert r.status_code in (401, 403)  # 403 when no admin key is configured
