"""Unit tests for the routing rules, privacy module and circuit breaker (no network, no database)."""
import asyncio
import os

import pytest

from router.adapters import CircuitBreaker, ModelReply, ModelUnavailable
from router.egress import EgressGateway
from router.orchestrator import JuniorsUnavailable, Orchestrator, Overloaded
from router.privacy import NON_SENSITIVE, SENSITIVE, classify, keyed_hash, mask

os.environ.setdefault("DATABASE_URL", "postgresql://unused")
os.environ.setdefault("ROUTER_API_KEY_SHA256", "0" * 64)
os.environ.setdefault("ROUTER_HASH_KEY", "k")
os.environ.setdefault("PROMPTS_FILE", os.path.join(os.path.dirname(__file__), "..", "prompts", "junior_prompts_v2.json"))
from router.config import Settings  # noqa: E402

OPTS = ["Mars", "Jupiter", "Venus", "Earth"]


class FakeModel:
    def __init__(self, behaviour: str):
        self.behaviour, self.calls = behaviour, 0

    async def ask(self, system, user, max_tokens):
        self.calls += 1
        if self.behaviour == "down":
            raise ModelUnavailable("down")
        if self.behaviour == "slow":
            await asyncio.sleep(0.2)
            return ModelReply("A", 10, 1, 200)
        return ModelReply(self.behaviour, 10, 1, 5)


class FakeStore:
    def __init__(self, budget_ok=True):
        self.rows, self.budget_ok = [], budget_ok

    def enqueue_log(self, row):
        self.rows.append(row)

    async def reserve_spend(self, usd):
        return self.budget_ok

    async def settle_spend(self, reserved, actual):
        pass


def make(j1="A", j2="A", senior="B", budget_ok=True):
    store = FakeStore(budget_ok)
    sen = FakeModel(senior)
    gw = EgressGateway(sen, store, 10, 50)
    orch = Orchestrator(Settings(), FakeModel(j1), FakeModel(j2), gw, store)
    return orch, sen, store


def run(orch, mode="balanced", sensitive=False, question="Which planet is largest?"):
    return asyncio.run(orch.answer(question, "choice", OPTS, mode, sensitive))


# ---------- decision rules (rule-v0) ----------

def test_agree_accepts_without_senior():
    orch, sen, _ = make("A", "A")
    o = run(orch)
    assert (o.status, o.answer, o.verified, o.escalated, sen.calls) == ("answered", "A", True, False, 0)


def test_disagree_balanced_escalates():
    orch, sen, _ = make("A", "C", senior="B")
    o = run(orch)
    assert (o.status, o.answer, o.escalated, o.reason, sen.calls) == ("answered", "B", True, "escalated_disagreement", 1)


def test_disagree_cheapest_returns_unverified_and_never_pays():
    orch, sen, _ = make("A", "C")
    o = run(orch, mode="cheapest")
    assert (o.status, o.answer, o.verified, sen.calls) == ("answered", "A", False, 0)


def test_best_quality_goes_straight_to_senior():
    orch, sen, _ = make("A", "A", senior="D")
    o = run(orch, mode="best_quality")
    assert (o.answer, o.reason, sen.calls) == ("D", "best_quality_mode", 1)


@pytest.mark.parametrize("mode", ["cheapest", "balanced", "best_quality"])
def test_sensitive_disagreement_abstains_in_every_mode(mode):
    orch, sen, _ = make("A", "C")
    o = run(orch, mode=mode, sensitive=True)
    assert (o.status, o.answer, o.reason, sen.calls) == ("abstained", None, "sensitive_disagreement", 0)


def test_pii_in_unflagged_request_is_treated_as_sensitive():
    orch, sen, store = make("A", "C")
    o = run(orch, question="Reply to sara@example.com about Jupiter")
    assert (o.data_class, o.status, sen.calls) == (SENSITIVE, "abstained", 0)
    assert store.rows[0]["prompt_masked"] is None          # sensitive: text never stored


def test_senior_down_abstains_instead_of_unverified_answer():
    orch, _, _ = make("A", "C", senior="down")
    o = run(orch)
    assert (o.status, o.reason) == ("abstained", "senior_unavailable")


def test_unreadable_senior_answer_abstains():
    orch, _, _ = make("A", "C", senior="I am not sure")
    assert run(orch).reason == "senior_unreadable"


def test_budget_cap_abstains_without_calling_senior():
    orch, sen, _ = make("A", "C", budget_ok=False)
    o = run(orch)
    assert (o.reason, sen.calls) == ("budget_exceeded", 0)


def test_one_junior_down_counts_as_disagreement():
    orch, sen, _ = make("A", "down", senior="A")
    o = run(orch)
    assert (o.escalated, sen.calls) == (True, 1)


def test_both_juniors_down_is_503_not_a_paid_escalation():
    orch, sen, _ = make("down", "down")
    with pytest.raises(JuniorsUnavailable):
        run(orch)
    assert sen.calls == 0


def test_overload_rejects_instead_of_escalating():
    orch, sen, _ = make("slow", "slow")
    orch.cap = asyncio.Semaphore(1)

    async def two():
        return await asyncio.gather(orch.answer("q", "choice", OPTS, "balanced", False),
                                    orch.answer("q", "choice", OPTS, "balanced", False),
                                    return_exceptions=True)
    results = asyncio.run(two())
    assert any(isinstance(r, Overloaded) for r in results)
    assert sen.calls == 0


def test_every_response_carries_versions():
    orch, _, store = make()
    o = run(orch)
    assert set(o.versions) == {"api", "policy", "prompt", "junior1", "junior2", "senior"}
    assert store.rows[0]["policy_version"] == "rule-v0"


def test_log_row_never_contains_raw_model_text():
    orch, _, store = make("Answer: A because the user said so", "A")
    run(orch)
    assert store.rows[0]["j1_answer"] == "A"     # normalized option only


# ---------- privacy ----------

@pytest.mark.parametrize("text", [
    "mail me at a.b+c@example.co.uk", "call +971 50 123 4567", "my number is 050 123 4567",
    "Emirates ID 784-1990-1234567-1", "card 4111 1111 1111 1111",
])
def test_pii_detected(text):
    assert classify(text, False) == SENSITIVE


@pytest.mark.parametrize("text", ["Which planet is largest?", "Order 12345 arrived late", "card 1234 5678 9012 3456"])
def test_plain_text_not_sensitive(text):
    assert classify(text, False) == NON_SENSITIVE   # last one fails the Luhn check


def test_flag_always_wins():
    assert classify("nothing personal here", True) == SENSITIVE


def test_mask_and_hash():
    m = mask("Email sara@example.com, card 4111-1111-1111-1111, phone +971 50 123 4567")
    assert "sara" not in m and "4111" not in m and "123 4567" not in m
    assert keyed_hash("x", "k1") != keyed_hash("x", "k2")


# ---------- circuit breaker ----------

def test_breaker_opens_after_threshold_and_half_opens_after_cooldown():
    b = CircuitBreaker(threshold=2, cooldown_s=0.05)
    b.record(False)
    assert b.allow()
    b.record(False)
    assert not b.allow()
    import time
    time.sleep(0.06)
    assert b.allow()
    b.record(True)
    assert b.failures == 0
