"""Audit sampler (ADR-26, feedback A1): eligibility, sampling, safety and failure handling."""

import asyncio
import os
import random
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(__file__))
from test_router_rules import OPTS, FakeModel  # noqa: E402

from router.audit import AuditSampler  # noqa: E402
from router.config import Settings  # noqa: E402
from router.egress import EgressGateway  # noqa: E402
from router.orchestrator import Orchestrator  # noqa: E402


class Store:
    def __init__(self, budget_ok=True):
        self.rows, self.audits, self.budget_ok = [], [], budget_ok

    def enqueue_log(self, row):
        self.rows.append(row)

    def enqueue_audit(self, result):
        self.audits.append(result)

    async def reserve_spend(self, usd):
        return self.budget_ok

    async def settle_spend(self, reserved, actual):
        pass


def outcome(**over):
    base = dict(
        request_id="req_1",
        status="answered",
        answer="A",
        verified=True,
        escalated=False,
        data_class="non_sensitive",
    )
    return SimpleNamespace(**(base | over))


def sampler(senior="A", rate=1.0, budget_ok=True, **kw):
    store = Store(budget_ok)
    senior_model = FakeModel(senior)
    gw = EgressGateway(senior_model, store, 10, 50)
    return AuditSampler(gw, store, rate=rate, rng=random.Random(0), **kw), senior_model, store


def run_audit(s, o):
    async def go():
        scheduled = s.maybe_schedule(o, "choice", "sys", "user", 32, OPTS)
        await s.drain()
        return scheduled

    return asyncio.run(go())


def test_agreeing_senior_is_recorded_as_agree():
    s, senior, store = sampler(senior="A")
    assert run_audit(s, outcome()) is True
    assert senior.calls == 1 and s.results["agree"] == 1
    assert store.audits == [
        {
            "request_id": "req_1",
            "audit_answer": "A",
            "audit_agrees": True,
            "audit_cost_usd": store.audits[0]["audit_cost_usd"],
        }
    ]


def test_disagreement_goes_to_the_review_queue_not_the_answer():
    s, _, store = sampler(senior="C")
    o = outcome()
    run_audit(s, o)
    assert s.results["disagree"] == 1 and store.audits[0]["audit_agrees"] is False
    assert o.answer == "A"  # the response is never changed


def test_only_accepted_verified_non_escalated_non_sensitive_answers_are_eligible():
    s, senior, _ = sampler()
    for o in (
        outcome(status="abstained"),
        outcome(verified=False),
        outcome(escalated=True),
        outcome(data_class="sensitive"),
    ):
        assert run_audit(s, o) is False
    assert senior.calls == 0


def test_rate_zero_never_audits_and_rate_controls_the_share():
    s0, senior0, _ = sampler(rate=0.0)
    assert not any(s0.maybe_schedule(outcome(), "choice", "s", "u", 32, OPTS) for _ in range(200))
    s5, _, _ = sampler(rate=0.05)
    picked = sum(s5.rng.random() < s5.rate for _ in range(10_000))
    assert 400 < picked < 600


def test_sensitive_data_is_refused_by_the_gateway_even_if_an_audit_were_forced():
    s, senior, store = sampler()
    asyncio.run(s._audit(outcome(data_class="sensitive"), "choice", "s", "u", 32, OPTS))
    assert senior.calls == 0 and s.skipped["egress_blocked"] == 1 and store.audits == []


def test_budget_and_senior_failures_are_skips_never_errors():
    s, senior, _ = sampler(budget_ok=False)
    run_audit(s, outcome())
    assert s.skipped["budget"] == 1 and senior.calls == 0

    down, _, _ = sampler(senior="down")
    run_audit(down, outcome())
    assert down.skipped["senior_down"] == 1


def test_unreadable_senior_answer_is_counted_but_not_called_a_disagreement():
    s, _, store = sampler(senior="no idea")
    run_audit(s, outcome())
    assert s.results["unreadable"] == 1 and s.results["disagree"] == 0
    assert store.audits[0]["audit_agrees"] is None


def test_backlog_limit_skips_instead_of_queuing_without_bound():
    s, _, _ = sampler(max_pending=0)
    assert s.maybe_schedule(outcome(), "choice", "s", "u", 32, OPTS) is False
    assert s.skipped["backlog_full"] == 1


def test_orchestrator_returns_before_the_audit_and_audits_only_accepted_answers():
    async def go():
        store = Store()
        senior = FakeModel("A")
        gw = EgressGateway(senior, store, 10, 50)
        audit = AuditSampler(gw, store, rate=1.0, rng=random.Random(0))
        orch = Orchestrator(Settings(), FakeModel("A"), FakeModel("A"), gw, store, audit=audit)
        o = await orch.answer("Which planet is largest?", "choice", OPTS, "balanced", False)
        calls_at_return = senior.calls
        await audit.drain()
        return o, calls_at_return, senior.calls, store

    o, at_return, after, store = asyncio.run(go())
    assert o.status == "answered" and not o.escalated
    assert at_return == 0 and after == 1  # the audit ran after the response was produced
    assert store.rows[0]["request_id"] == store.audits[0]["request_id"]


def test_invalid_rate_is_rejected():
    import pytest

    with pytest.raises(ValueError):
        AuditSampler(SimpleNamespace(), Store(), rate=1.5)
