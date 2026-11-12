"""Demo page (FR-15) and model warm-up: served safely, its replay questions are valid requests,
and warm-up never blocks or breaks start-up."""

import asyncio
import hashlib
import json
import logging
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))
from test_router_rules import FakeModel  # noqa: E402

from router import app as app_module  # noqa: E402
from router.app import AnswerRequest, warm_up  # noqa: E402

KEY = "demo-test-key"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ROUTER_API_KEY_SHA256", hashlib.sha256(KEY.encode()).hexdigest())
    from router.config import Settings

    app_module.app.state.settings = Settings()
    return TestClient(app_module.app)


def test_demo_page_is_served_with_a_strict_content_policy(client):
    r = client.get("/demo")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    csp = r.headers["content-security-policy"]
    assert "connect-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert r.headers["cache-control"] == "no-store"
    page = r.text
    assert "/v1/answer" in page and "/demo/samples" in page
    # No credential in the page, and model output is never parsed as HTML (no XSS path).
    assert KEY not in page and app_module.app.state.settings.api_key_sha256 not in page
    assert "innerHTML" not in page and "localStorage" not in page


def test_demo_page_needs_no_key_but_the_api_still_does(client):
    assert client.get("/demo").status_code == 200
    body = {"question": "Which planet is closest to the Sun?", "task_type": "choice"}
    body |= {"options": ["Venus", "Mercury", "Earth", "Mars"]}
    assert client.post("/v1/answer", json=body).status_code == 401


def test_every_replay_question_is_a_valid_request_with_a_reachable_answer(client):
    data = client.get("/demo/samples").json()
    items, labels = data["items"], data["labels"]
    assert len(items) == 40 and len(labels) == 77
    assert {i["task_type"] for i in items} == {"choice", "label"}
    for it in items:
        options = it["options"] if it["task_type"] == "choice" else labels
        AnswerRequest(question=it["question"], task_type=it["task_type"], options=options)
        assert it["gold"] in ("ABCD" if it["task_type"] == "choice" else labels)


def test_replay_questions_come_from_the_test_split_only():
    from common.splits import split_of
    from pipelines.build_replay import group_key

    path = os.path.join(os.path.dirname(app_module.__file__), "static", "demo_samples.json")
    for it in json.load(open(path, encoding="utf-8"))["items"]:
        assert split_of(group_key(it["task_type"], it["question"])) == "test", it["id"]


def test_warm_up_asks_each_local_model_once(caplog):
    j1, j2 = FakeModel("A"), FakeModel("B")
    with caplog.at_level(logging.INFO, logger="router"):
        asyncio.run(warm_up(j1, j2))
    assert (j1.calls, j2.calls) == (1, 1)
    assert "warm-up: 2/2 local models ready" in caplog.text


def test_warm_up_failure_is_logged_not_raised(caplog):
    with caplog.at_level(logging.INFO, logger="router"):
        asyncio.run(warm_up(FakeModel("A"), FakeModel("down")))
    assert "warm-up: 1/2 local models ready" in caplog.text
