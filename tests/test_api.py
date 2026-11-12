"""HTTP contract tests (interfaces I1 and I2) against the real FastAPI app with fake back-ends.

The lifespan is not started (no database, no models): app.state is filled with fakes, so these
tests check authentication, validation, error codes and response shapes only.
"""

import hashlib

import pytest
from fastapi.testclient import TestClient

from router import app as app_module
from router.orchestrator import JuniorsUnavailable, Outcome, Overloaded

KEY = "test-key-123"
PLANETS = ["Venus", "Mercury", "Earth", "Mars"]
RID = "req_0123456789ab"


class FakeStore:
    def __init__(self, up=True, known=True, fail=False):
        self.up, self.known, self.fail, self.feedback = up, known, fail, []

    async def ping(self):
        return self.up

    async def upsert_feedback(self, request_id, key_id, flag, note):
        if self.fail:
            raise ConnectionError("db down")
        self.feedback.append((request_id, key_id, flag, note))
        return self.known


class FakeOrch:
    def __init__(self, raises=None):
        self.raises, self.calls = raises, 0

    async def answer(self, question, task_type, options, mode, sensitive):
        self.calls += 1
        if self.raises:
            raise self.raises
        return Outcome(
            RID,
            "answered",
            "B",
            True,
            False,
            "gemma3:12b",
            "policy_accepted",
            "non_sensitive",
            0.0,
            1234,
            {"api": "v1", "policy": "run-x"},
            0.83,
        )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("ROUTER_API_KEY_SHA256", hashlib.sha256(KEY.encode()).hexdigest())
    monkeypatch.setenv("ROUTER_HASH_KEY", "k")
    from router.config import Settings

    state = app_module.app.state
    state.settings, state.store, state.orch = Settings(), FakeStore(), FakeOrch()
    return TestClient(app_module.app)


def ask(client, key=KEY, **over):
    body = {
        "question": "Which planet is closest to the Sun?",
        "task_type": "choice",
        "options": PLANETS,
        "mode": "balanced",
    } | over
    return client.post("/v1/answer", json=body, headers={"X-API-Key": key} if key else {})


def test_answer_returns_the_routing_record(client):
    r = ask(client)
    assert r.status_code == 200
    body = r.json()
    assert (body["request_id"], body["answer"], body["reason"], body["policy_p"]) == (
        RID,
        "B",
        "policy_accepted",
        0.83,
    )


@pytest.mark.parametrize("key", [None, "wrong-key"])
def test_missing_or_wrong_key_is_401_and_reveals_nothing(client, key):
    r = ask(client, key=key, task_type="not-a-type")  # invalid body too: auth is checked first
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


@pytest.mark.parametrize(
    "over",
    [
        {"options": ["Venus", "Mercury", "Earth"]},  # choice needs exactly 4
        {"options": ["Venus", "venus", "Earth", "Mars"]},  # duplicates
        {"task_type": "number"},  # dropped task type (ADR-20)
        {"question": "ما هو أقرب كوكب إلى الشمس؟"},  # English only (ADR-21): Arabic rejected
        {"question": ""},
    ],
)
def test_invalid_requests_are_400_without_echoing_input(client, over):
    r = ask(client, **over)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request"
    assert "الشمس" not in r.text


def test_database_down_is_503_before_any_model_call(client):
    client.app.state.store = FakeStore(up=False)
    r = ask(client)
    assert r.status_code == 503 and r.json()["error"]["code"] == "database_unavailable"
    assert client.app.state.orch.calls == 0


@pytest.mark.parametrize(
    "exc, code", [(Overloaded(), "overloaded"), (JuniorsUnavailable(), "models_unavailable")]
)
def test_overload_and_juniors_down_are_503(client, exc, code):
    client.app.state.orch = FakeOrch(raises=exc)
    r = ask(client)
    assert r.status_code == 503 and r.json()["error"]["code"] == code


def test_health_and_ready(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").status_code == 200
    client.app.state.store = FakeStore(up=False)
    assert client.get("/ready").status_code == 503


# ---------------------------------------------------------------- feedback (I2)


def fb(client, key=KEY, **over):
    body = {"request_id": RID, "flag": "wrong"} | over
    return client.post("/v1/feedback", json=body, headers={"X-API-Key": key} if key else {})


def test_feedback_is_received_and_the_note_is_masked(client):
    r = fb(client, note="customer card 4111 1111 1111 1111 was misrouted")
    assert r.status_code == 200 and r.json() == {"received": True}
    ((rid, key_id, flag, note),) = client.app.state.store.feedback
    assert (rid, flag) == (RID, "wrong") and len(key_id) == 12
    assert "4111" not in note and "[CARD]" in note


def test_feedback_for_an_unknown_request_is_404(client):
    client.app.state.store = FakeStore(known=False)
    r = fb(client)
    assert r.status_code == 404 and r.json()["error"]["code"] == "unknown_request"


@pytest.mark.parametrize(
    "over",
    [{"flag": "maybe"}, {"request_id": "not-an-id"}, {"note": "x" * 501}],
)
def test_invalid_feedback_is_400(client, over):
    assert fb(client, **over).status_code == 400


def test_feedback_needs_the_api_key(client):
    assert fb(client, key=None).status_code == 401


def test_feedback_with_the_database_down_is_503(client):
    client.app.state.store = FakeStore(fail=True)
    assert fb(client).status_code == 503


def test_same_caller_same_request_is_an_upsert_key(client):
    fb(client, flag="wrong")
    fb(client, flag="correct")
    keys = {(rid, key_id) for rid, key_id, _, _ in client.app.state.store.feedback}
    assert len(keys) == 1  # the store upserts on (request_id, key_id)


def test_known_limitation_latin_script_languages_pass_the_english_check(client):
    """Characterizes a limitation found by these tests (report, limitations): the ADR-21 check
    rejects non-Latin scripts such as Arabic, but Spanish with few accents passes as English."""
    assert ask(client, question="¿Cuál es el planeta más cercano al Sol?").status_code == 200
