"""Hot reload client (pipelines/serving.py, interface I7): the order of the two calls, failure
handling, and the HTTP helper against a real local server. No MLServer or router needed."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from pipelines import serving

PS, ROUTER, ADMIN = "http://policy-server:8080", "http://router:8000/", "admin-key"


class FakeSend:
    def __init__(self, load=200, reload=200, reload_body=None):
        self.calls, self.load, self.reload = [], load, reload
        self.reload_body = reload_body or {"reloaded": True, "previous": "run-a", "policy": "run-b"}

    def __call__(self, url, headers=None):
        self.calls.append((url, headers))
        if url.endswith("/admin/policy/reload"):
            return self.reload, self.reload_body
        return self.load, {}


def test_mlserver_loads_first_then_the_router_switches():
    send = FakeSend()
    body = serving.activate(PS, ROUTER, ADMIN, send=send)
    assert [u for u, _ in send.calls] == [
        "http://policy-server:8080/v2/repository/models/routing-policy/load",
        "http://router:8000/admin/policy/reload",
    ]
    assert send.calls[0][1] is None  # MLServer never receives the admin key
    assert send.calls[1][1] == {"X-Admin-Key": ADMIN}
    assert body["policy"] == "run-b"


def test_router_is_not_asked_when_mlserver_cannot_load():
    send = FakeSend(load=400)
    with pytest.raises(serving.ActivationError, match="did not load"):
        serving.activate(PS, ROUTER, ADMIN, send=send)
    assert len(send.calls) == 1


def test_router_refusal_is_reported_with_its_code():
    body = {
        "error": {"code": "reload_failed", "message": "model_version_mismatch; run-a keeps serving"}
    }
    with pytest.raises(serving.ActivationError, match="reload_failed.*run-a keeps serving"):
        serving.activate(PS, ROUTER, ADMIN, send=FakeSend(reload=409, reload_body=body))


def test_no_champion_unloads_and_the_router_moves_to_rule_v0():
    send = FakeSend(load=404, reload_body={"previous": "run-a", "policy": "rule-v0"})
    body = serving.activate(PS, ROUTER, ADMIN, champion_present=False, send=send)
    assert send.calls[0][0].endswith("/routing-policy/unload") and body["policy"] == "rule-v0"


def test_from_env_needs_all_three(monkeypatch):
    for name in serving.ENV:
        monkeypatch.setenv(name, "x")
    assert serving.from_env() == ("x", "x", "x")
    monkeypatch.setenv("ROUTER_ADMIN_KEY", "")
    assert serving.from_env() is None


# ---------------------------------------------------------------- the HTTP helper


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        seen.append((self.path, self.headers.get("X-Admin-Key")))
        status, body = routes.get(self.path, (404, b'{"error": {"code": "not_found"}}'))
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


seen: list = []
routes = {
    "/v2/repository/models/routing-policy/load": (200, b""),
    "/admin/policy/reload": (200, json.dumps({"previous": "run-a", "policy": "run-b"}).encode()),
    "/broken": (500, b"not json"),
}


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_activate_over_real_http(server):
    seen.clear()
    body = serving.activate(server, server, ADMIN)
    assert body == {"previous": "run-a", "policy": "run-b"}
    assert seen == [
        ("/v2/repository/models/routing-policy/load", None),
        ("/admin/policy/reload", ADMIN),
    ]


def test_post_returns_error_statuses_and_tolerates_non_json(server):
    assert serving.post(f"{server}/missing")[0] == 404
    assert serving.post(f"{server}/broken") == (500, {"detail": "not json"})


def test_post_raises_when_the_service_is_down():
    with pytest.raises(serving.ActivationError, match="unreachable"):
        serving.post("http://127.0.0.1:9/x", timeout=1)
