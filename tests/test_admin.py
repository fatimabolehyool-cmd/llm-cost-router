"""Admin interface I7: policy hot reload with a separate admin key, against the real FastAPI app.

The policy server is a fake HTTP transport whose loaded model version the tests change, playing
MLServer before and after `pipelines.registry promote` loads a new model. The orchestrator is the
real one (with fake models), so a reload is checked all the way to the next routing decision.
"""

import asyncio
import hashlib
import json
import os
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))
from test_policy import reply, write_meta  # noqa: E402
from test_router_rules import OPTS, FakeModel, FakeStore  # noqa: E402

from router import app as app_module  # noqa: E402
from router import metrics  # noqa: E402
from router.config import Settings  # noqa: E402
from router.egress import EgressGateway  # noqa: E402
from router.orchestrator import Orchestrator  # noqa: E402
from router.policy import PolicyClient, PolicyMeta  # noqa: E402

KEY, ADMIN = "caller-key-123", "admin-key-456"
URL = "http://policy-server:8080"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Stack:
    """app.state wired with a real orchestrator and a fake policy server."""

    def __init__(self, monkeypatch, tmp_path, admin=ADMIN, policy_url=URL, start="run-old"):
        self.served = {"version": start}  # the model the fake MLServer has loaded
        self.meta_path = write_meta(tmp_path, model_version=start)
        monkeypatch.setenv("ROUTER_API_KEY_SHA256", sha(KEY))
        monkeypatch.setenv("ROUTER_ADMIN_KEY_SHA256", sha(admin) if admin else "")
        monkeypatch.setenv("POLICY_URL", policy_url)
        monkeypatch.setenv("POLICY_META_FILE", str(self.meta_path))
        self.settings = Settings()
        self.http = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: reply(0.9, self.served["version"]))
        )
        store = FakeStore()
        initial = PolicyClient(self.http, URL, PolicyMeta.load(self.meta_path)) if start else None
        self.orch = Orchestrator(
            self.settings,
            FakeModel("A"),
            FakeModel("C"),  # a disagreement: only the policy can accept it
            EgressGateway(FakeModel("B"), store, 10, 50),
            store,
            initial,
        )
        st = app_module.app.state
        st.settings, st.store, st.orch = self.settings, store, self.orch
        st.http, st.reload_lock = self.http, asyncio.Lock()
        self.client = TestClient(app_module.app)

    def export(self, version: str, **over) -> None:
        """What `pipelines.registry export` does: replace the champion's metadata on disk."""
        write_meta(self.meta_path.parent, model_version=version, **over)

    def reload(self, key=ADMIN):
        return self.client.post("/admin/policy/reload", headers={"X-Admin-Key": key})

    def answer(self):
        return asyncio.run(
            self.orch.answer("Which planet is largest?", "choice", OPTS, "balanced", False)
        )


def reloads(result: str) -> float:
    return metrics.REGISTRY.get_sample_value("router_policy_reloads_total", {"result": result}) or 0


# ---------------------------------------------------------------- authentication


def test_admin_is_disabled_without_a_configured_key(monkeypatch, tmp_path):
    s = Stack(monkeypatch, tmp_path, admin="")
    r = s.reload()
    assert r.status_code == 403 and r.json()["error"]["code"] == "admin_disabled"


@pytest.mark.parametrize(
    "headers",
    [{}, {"X-Admin-Key": "wrong"}, {"X-Admin-Key": KEY}, {"X-API-Key": KEY}],
    ids=["none", "wrong", "caller-key-as-admin", "caller-key-header"],
)
def test_admin_needs_the_admin_key_and_never_accepts_the_caller_key(monkeypatch, tmp_path, headers):
    s = Stack(monkeypatch, tmp_path)
    for r in (
        s.client.get("/admin/policy", headers=headers),
        s.client.post("/admin/policy/reload", headers=headers),
    ):
        assert r.status_code == 401
        assert r.json()["error"] == {
            "code": "unauthorized",
            "message": "missing or invalid admin key",
        }
    assert s.orch.versions["policy"] == "run-old"


def test_admin_key_must_differ_from_the_caller_key(monkeypatch):
    monkeypatch.setenv("ROUTER_API_KEY_SHA256", sha(KEY))
    monkeypatch.setenv("ROUTER_ADMIN_KEY_SHA256", sha(KEY))
    with pytest.raises(RuntimeError, match="differ"):
        Settings()
    monkeypatch.setenv("ROUTER_ADMIN_KEY_SHA256", "not-a-hash")
    with pytest.raises(RuntimeError, match="SHA-256"):
        Settings()


# ---------------------------------------------------------------- reload


def test_policy_state_shows_what_is_serving(monkeypatch, tmp_path):
    s = Stack(monkeypatch, tmp_path)
    r = s.client.get("/admin/policy", headers={"X-Admin-Key": ADMIN})
    assert r.status_code == 200
    assert r.json() == {
        "policy": "run-old",
        "feature_version": "policy-features-v2",
        "threshold_balanced": 0.6,
    }


def test_reload_switches_the_policy_without_a_restart(monkeypatch, tmp_path):
    s = Stack(monkeypatch, tmp_path)
    assert s.answer().versions["policy"] == "run-old"
    ok_before = reloads("ok")

    s.served["version"] = "run-new"  # MLServer has loaded the promoted model ...
    s.export("run-new", thresholds={"balanced": 0.7})  # ... and its metadata is on disk
    r = s.reload()

    assert r.status_code == 200
    assert r.json() == {
        "reloaded": True,
        "previous": "run-old",
        "policy": "run-new",
        "feature_version": "policy-features-v2",
        "threshold_balanced": 0.7,
    }
    out = s.answer()  # the very next request, same process
    assert out.versions["policy"] == "run-new" and out.reason == "policy_accepted"
    assert reloads("ok") == ok_before + 1


def test_reload_is_refused_while_the_server_runs_another_model(monkeypatch, tmp_path):
    s = Stack(monkeypatch, tmp_path)
    failed_before = reloads("failed")
    s.export("run-new")  # metadata exported, but MLServer still serves run-old
    r = s.reload()

    assert r.status_code == 409
    assert r.json()["error"]["code"] == "reload_failed"
    assert "model_version_mismatch" in r.json()["error"]["message"]
    assert "run-old keeps serving" in r.json()["error"]["message"]
    assert s.orch.versions["policy"] == "run-old"
    assert reloads("failed") == failed_before + 1


@pytest.mark.parametrize(
    "over",
    [{"thresholds": {"balanced": 1.5}}, {"feature_version": "policy-features-v9"}],
    ids=["bad-threshold", "unknown-features"],
)
def test_reload_is_refused_for_broken_metadata(monkeypatch, tmp_path, over):
    s = Stack(monkeypatch, tmp_path)
    s.served["version"] = "run-new"
    s.export("run-new", **over)
    r = s.reload()
    assert r.status_code == 409 and r.json()["error"]["code"] == "reload_failed"
    assert s.orch.versions["policy"] == "run-old"


def test_reload_is_refused_when_the_policy_server_is_down(monkeypatch, tmp_path):
    s = Stack(monkeypatch, tmp_path)

    def down(req):
        raise httpx.ConnectError("refused")

    app_module.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(down))
    s.export("run-new")
    r = s.reload()
    assert r.status_code == 409 and "ConnectError" in r.json()["error"]["message"]
    assert s.orch.versions["policy"] == "run-old"


def test_no_exported_champion_means_rule_v0(monkeypatch, tmp_path):
    s = Stack(monkeypatch, tmp_path)
    s.meta_path.unlink()  # `registry export` removes the folder when there is no champion
    r = s.reload()
    assert r.status_code == 200
    assert r.json()["previous"] == "run-old" and r.json()["policy"] == "rule-v0"
    assert s.answer().versions["policy"] == "rule-v0"  # disagreement -> escalated by the rule


def test_reload_from_rule_v0_to_a_trained_model(monkeypatch, tmp_path):
    s = Stack(monkeypatch, tmp_path, start=None)
    s.served["version"] = "run-first"
    s.export("run-first")
    r = s.reload()
    assert r.status_code == 200 and (r.json()["previous"], r.json()["policy"]) == (
        "rule-v0",
        "run-first",
    )


def test_reload_without_a_policy_server_configured(monkeypatch, tmp_path):
    s = Stack(monkeypatch, tmp_path, policy_url="", start=None)
    r = s.reload()
    assert r.status_code == 409 and r.json()["error"]["code"] == "policy_not_configured"


def test_reload_response_never_echoes_the_metadata_file(monkeypatch, tmp_path):
    s = Stack(monkeypatch, tmp_path)
    s.served["version"] = "run-new"
    s.export("run-new")
    body = json.dumps(s.reload().json())
    assert "answer_prior" not in body and str(tmp_path) not in body
