"""Policy client (interface I5), model metadata and the orchestrator's policy decisions.

No MLServer needed: a fake HTTP transport plays the policy server. The integration test against
the real MLServer container is part of the Compose checks.
"""

import asyncio
import json
import math
import os
import sys

import httpx
import pytest

from common.policy_features import FEATURES, FEATURES_V2, fit_answer_prior
from router.policy import PolicyClient, PolicyMeta, PolicyUnavailable

URL = "http://policy-server:8080"
PRIOR = fit_answer_prior(
    [{"task_type": "choice", "j1_answer": "A", "j2_answer": "A", "junior_correct": 1}] * 4
)


def write_meta(tmp_path, **over):
    meta = {
        "model_version": "run-abc12345",
        "feature_version": "policy-features-v2",
        "features": FEATURES_V2,
        "thresholds": {"balanced": 0.6},
        "answer_prior": PRIOR,
    } | over
    path = tmp_path / "policy_meta.json"
    path.write_text(json.dumps(meta))
    return path


def reply(p=0.8, version="run-abc12345", name="predict_proba"):
    return httpx.Response(
        200,
        json={
            "model_name": "routing-policy",
            "model_version": version,
            "outputs": [{"name": name, "shape": [1, 2], "datatype": "FP64", "data": [1 - p, p]}],
        },
    )


def client_with(handler, meta):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return PolicyClient(http, URL, meta)


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- metadata


def test_meta_loads_v2(tmp_path):
    m = PolicyMeta.load(write_meta(tmp_path))
    assert (m.model_version, m.feature_version, m.threshold_balanced) == (
        "run-abc12345",
        "policy-features-v2",
        0.6,
    )


@pytest.mark.parametrize(
    "over",
    [
        {"feature_version": "policy-features-v9"},
        {"features": FEATURES},  # v2 label with the v1 feature list
        {"answer_prior": None},
        {"thresholds": {"balanced": 1.5}},
    ],
)
def test_inconsistent_meta_is_rejected(tmp_path, over):
    with pytest.raises(ValueError):
        PolicyMeta.load(write_meta(tmp_path, **over))


def test_v1_meta_needs_no_prior(tmp_path):
    m = PolicyMeta.load(
        write_meta(
            tmp_path, feature_version="policy-features-v1", features=FEATURES, answer_prior=None
        )
    )
    assert len(m.vector("choice", 4, "A", "A", 100, 1, 1)) == 9


def test_vector_matches_the_training_contract(tmp_path):
    m = PolicyMeta.load(write_meta(tmp_path))
    v = m.vector("choice", 4, "A", None, 120, 2, 0)
    assert len(v) == len(FEATURES_V2) == 10
    assert v[3] == 0.0 and v[4] == 1.0 and v[5] == 0.0  # disagree, j1 readable, j2 not
    assert v[-1] == pytest.approx(PRIOR["answers"]["choice"]["A"])


# ---------------------------------------------------------------- client


def test_request_and_probability(tmp_path):
    seen = {}

    def handler(request):
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        return reply(0.73)

    pc = client_with(handler, PolicyMeta.load(write_meta(tmp_path)))
    assert run(pc.p_correct([0.0] * 10)) == pytest.approx(0.73)
    assert seen["url"] == f"{URL}/v2/models/routing-policy/infer"
    inp = seen["body"]["inputs"][0]
    assert (inp["name"], inp["shape"], inp["datatype"]) == ("features", [1, 10], "FP32")
    assert seen["body"]["outputs"] == [{"name": "predict_proba"}]


@pytest.mark.parametrize("bad", [[0.0] * 9, [0.0] * 9 + [math.nan], [0.0] * 9 + [math.inf]])
def test_invalid_vector_is_never_sent(tmp_path, bad):
    calls = []
    pc = client_with(lambda r: calls.append(1) or reply(), PolicyMeta.load(write_meta(tmp_path)))
    with pytest.raises(PolicyUnavailable, match="invalid_features"):
        run(pc.p_correct(bad))
    assert calls == []


def test_dropped_connection_is_retried_once(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.RemoteProtocolError("Server disconnected", request=request)
        return reply(0.9)

    pc = client_with(handler, PolicyMeta.load(write_meta(tmp_path)))
    assert run(pc.p_correct([0.0] * 10)) == pytest.approx(0.9)
    assert len(calls) == 2


@pytest.mark.parametrize(
    "handler, reason",
    [
        (lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=r)), "timeout"),
        (lambda r: (_ for _ in ()).throw(httpx.ConnectError("down", request=r)), "ConnectError"),
        (lambda r: httpx.Response(500), "http_500"),
        (lambda r: reply(version="run-other"), "model_version_mismatch"),
        (lambda r: reply(name="predict"), "malformed_reply"),
        (lambda r: reply(p=1.7), "probability_out_of_range"),
        (lambda r: httpx.Response(200, text="not json"), "malformed_reply"),
    ],
)
def test_every_failure_becomes_policy_unavailable(tmp_path, handler, reason):
    pc = client_with(handler, PolicyMeta.load(write_meta(tmp_path)))
    with pytest.raises(PolicyUnavailable, match=reason):
        run(pc.p_correct([0.0] * 10))


# ---------------------------------------------------------------- orchestrator with a policy

sys.path.insert(0, os.path.dirname(__file__))
from test_router_rules import OPTS, FakeModel, FakeStore  # noqa: E402

from router.config import Settings  # noqa: E402
from router.egress import EgressGateway  # noqa: E402
from router.orchestrator import Orchestrator  # noqa: E402


class FakePolicy:
    def __init__(self, meta, p=None, fail=False):
        self.meta, self.p, self.fail, self.calls = meta, p, fail, 0

    async def p_correct(self, x):
        self.calls += 1
        assert len(x) == len(self.meta.features)
        if self.fail:
            raise PolicyUnavailable("down")
        return self.p


def make(tmp_path, j1="A", j2="A", p=0.9, fail=False):
    store = FakeStore()
    senior = FakeModel("B")
    policy = FakePolicy(PolicyMeta.load(write_meta(tmp_path)), p=p, fail=fail)
    orch = Orchestrator(
        Settings(),
        FakeModel(j1),
        FakeModel(j2),
        EgressGateway(senior, store, 10, 50),
        store,
        policy,
    )
    return orch, senior, store, policy


def answer(orch, mode="balanced", sensitive=False):
    return asyncio.run(orch.answer("Which planet is largest?", "choice", OPTS, mode, sensitive))


def test_high_probability_accepts_even_a_disagreement(tmp_path):
    orch, senior, store, _ = make(tmp_path, "A", "C", p=0.9)
    o = answer(orch)
    assert (o.status, o.answer, o.verified, o.reason, senior.calls) == (
        "answered",
        "A",
        True,
        "policy_accepted",
        0,
    )
    assert o.versions["policy"] == "run-abc12345"
    assert store.rows[0]["policy_p"] == pytest.approx(0.9)
    assert store.rows[0]["policy_version"] == "run-abc12345"


def test_low_probability_escalates_even_an_agreement(tmp_path):
    orch, senior, _, _ = make(tmp_path, "A", "A", p=0.2)
    o = answer(orch)
    assert (o.answer, o.escalated, o.reason, senior.calls) == (
        "B",
        True,
        "escalated_low_confidence",
        1,
    )


def test_low_probability_on_sensitive_abstains_and_never_pays(tmp_path):
    orch, senior, _, _ = make(tmp_path, "A", "A", p=0.2)
    o = answer(orch, sensitive=True)
    assert (o.status, o.reason, senior.calls) == ("abstained", "sensitive_low_confidence", 0)


def test_policy_down_falls_back_to_the_agreement_rule(tmp_path):
    orch, senior, store, _ = make(tmp_path, "A", "A", fail=True)
    o = answer(orch)
    assert (o.status, o.reason, senior.calls) == ("answered", "juniors_agreed", 0)
    assert o.versions["policy"] == "rule-v0-fallback" and orch.policy_fallbacks == 1
    assert store.rows[0]["policy_p"] is None


def test_policy_down_and_disagreement_still_escalates(tmp_path):
    orch, senior, _, _ = make(tmp_path, "A", "C", fail=True)
    assert answer(orch).reason == "escalated_disagreement" and senior.calls == 1


def test_cheapest_and_best_quality_never_ask_the_policy(tmp_path):
    for mode in ("cheapest", "best_quality"):
        orch, _, _, policy = make(tmp_path, "A", "C", p=0.9)
        answer(orch, mode=mode)
        assert policy.calls == 0


def test_both_unreadable_skips_the_policy(tmp_path):
    orch, senior, _, policy = make(tmp_path, "no idea", "maybe", p=0.99)
    o = answer(orch)
    assert policy.calls == 0 and o.escalated and senior.calls == 1
