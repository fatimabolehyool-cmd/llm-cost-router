"""AnthropicSenior (interface I4) against a fake HTTP transport: no network, no key needed.

The response fixture follows the documented Messages API shape for Claude Opus 5.5 (a thinking
block with omitted text before the answer). It is replaced by a scrubbed recording of a real
response once the live smoke call has run (`make senior-smoke`).
"""

import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest

from router.adapters import AnthropicSenior, CircuitBreaker, HttpSenior, ModelUnavailable
from router.egress import EgressGateway

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "anthropic_messages_response.json").read_text()
)
EXPECTED_TEXT = "".join(b.get("text", "") for b in FIXTURE["content"] if b.get("type") == "text")
KEY = "sk-ant-test-0000000000000000"
URL = "https://api.anthropic.com/v1/messages"


def make_senior(handler, **kw):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    kw.setdefault("retry_delay_s", 0)
    return AnthropicSenior(client, URL, "claude-opus-5-5", KEY, timeout_s=5, **kw)


def ok(request):
    return httpx.Response(200, json=FIXTURE)


def run(coro):
    return asyncio.run(coro)


def test_request_matches_the_contract():
    seen = {}

    def handler(request):
        seen["url"], seen["headers"] = str(request.url), request.headers
        seen["body"] = json.loads(request.content)
        return ok(request)

    run(make_senior(handler).ask("sys", "user text", 32))
    assert seen["url"] == URL
    assert seen["headers"]["authorization"] == f"Bearer {KEY}"
    assert seen["headers"]["anthropic-version"] == "2026-10-21"
    body = seen["body"]
    assert body["model"] == "claude-opus-5-5" and body["system"] == "sys"
    assert body["messages"] == [{"role": "user", "content": "user text"}]
    assert body["max_tokens"] == 32 + 1024  # answer budget + thinking headroom
    assert body["output_config"] == {"effort": "low"}
    assert "temperature" not in body and "thinking" not in body


def test_reply_skips_thinking_blocks_and_reports_usage():
    reply = run(make_senior(ok).ask("s", "u", 32))
    assert reply.text == EXPECTED_TEXT and reply.text.strip()
    assert (reply.input_tokens, reply.output_tokens) == (
        FIXTURE["usage"]["input_tokens"],
        FIXTURE["usage"]["output_tokens"],
    )
    assert reply.stop_reason == FIXTURE["stop_reason"]


def test_optional_thinking_setting_is_sent_when_configured():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return ok(request)

    run(make_senior(handler, thinking={"type": "disabled"}, effort=None).ask("s", "u", 32))
    assert seen["body"]["thinking"] == {"type": "disabled"}
    assert "output_config" not in seen["body"]


@pytest.mark.parametrize("status", [429, 500, 529])
def test_retries_once_on_retryable_status(status):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(status) if len(calls) == 1 else ok(request)

    assert run(make_senior(handler).ask("s", "u", 32)).text == "B"
    assert len(calls) == 2


def test_gives_up_after_one_retry():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(529)

    with pytest.raises(ModelUnavailable, match="http_529"):
        run(make_senior(handler).ask("s", "u", 32))
    assert len(calls) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_never_retries_client_errors(status):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(status, json={"error": {"message": f"details with {KEY}"}})

    with pytest.raises(ModelUnavailable) as exc:
        run(make_senior(handler).ask("s", "u", 32))
    assert len(calls) == 1
    assert str(exc.value) == f"http_{status}"  # status only: no body, no key


def test_connection_failure_is_retried_once():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("refused", request=request)
        return ok(request)

    assert run(make_senior(handler).ask("s", "u", 32)).text == "B"
    assert len(calls) == 2


def test_read_timeout_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ModelUnavailable, match="ReadTimeout"):
        run(make_senior(handler).ask("s", "u", 32))
    assert len(calls) == 1  # the request may have been processed and billed


def test_breaker_opens_and_stops_calls():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400)

    senior = make_senior(handler, breaker=CircuitBreaker(threshold=2, cooldown_s=60))
    for _ in range(2):
        with pytest.raises(ModelUnavailable):
            run(senior.ask("s", "u", 32))
    with pytest.raises(ModelUnavailable, match="circuit_open"):
        run(senior.ask("s", "u", 32))
    assert len(calls) == 2


def test_key_never_appears_in_repr():
    assert KEY not in repr(make_senior(ok))


def test_unreadable_when_no_text_block():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "content": [{"type": "thinking", "thinking": ""}],
                "stop_reason": "max_tokens",
                "usage": {"input_tokens": 60, "output_tokens": 1056},
            },
        )

    reply = run(make_senior(handler).ask("s", "u", 32))
    assert reply.text == "" and reply.stop_reason == "max_tokens"  # normalizer -> unreadable


class SpyStore:
    def __init__(self):
        self.reserved, self.settled = [], []

    async def reserve_spend(self, usd):
        self.reserved.append(usd)
        return True

    async def settle_spend(self, reserved, actual):
        self.settled.append((reserved, actual))


def test_gateway_reserves_the_full_output_budget_and_settles_the_actual_cost():
    store = SpyStore()
    gateway = EgressGateway(make_senior(ok), store, price_in=4, price_out=20)
    reply, cost = run(gateway.ask_senior("non_sensitive", "s" * 40, "u" * 40, 32))
    expected_reserve = (80 / 4 * 4 + (32 + 1024) * 20) / 1e6
    assert store.reserved == [pytest.approx(expected_reserve)]
    usage = FIXTURE["usage"]
    assert cost == pytest.approx((usage["input_tokens"] * 4 + usage["output_tokens"] * 20) / 1e6)
    assert store.settled == [(pytest.approx(expected_reserve), pytest.approx(cost))]


# ---------------------------------------------------------------- configuration and wiring


def settings(**env):
    base = {
        "DATABASE_URL": "postgresql://x",
        "ROUTER_API_KEY_SHA256": "0" * 64,
        "ROUTER_HASH_KEY": "k",
    }
    old = dict(os.environ)
    os.environ.update(base | env)
    try:
        from router.config import Settings

        return Settings()
    finally:
        os.environ.clear()
        os.environ.update(old)


def test_mock_never_receives_the_real_key():
    from router.app import build_senior

    s = settings(SENIOR_PROVIDER="mock", SENIOR_API_KEY=KEY)
    senior = build_senior(s, httpx.AsyncClient(), CircuitBreaker())
    assert isinstance(senior, HttpSenior) and senior.api_key != KEY


def test_anthropic_provider_builds_the_real_adapter():
    from router.app import build_senior

    s = settings(
        SENIOR_PROVIDER="anthropic",
        SENIOR_URL=URL,
        SENIOR_API_KEY=KEY,
        SENIOR_MODEL="claude-opus-5-5",
    )
    assert isinstance(build_senior(s, httpx.AsyncClient(), CircuitBreaker()), AnthropicSenior)


@pytest.mark.parametrize(
    "env",
    [
        {"SENIOR_PROVIDER": "openai"},
        {
            "SENIOR_PROVIDER": "anthropic",
            "SENIOR_URL": "http://mock-models:9100",
            "SENIOR_API_KEY": KEY,
        },
        {
            "SENIOR_PROVIDER": "anthropic",
            "SENIOR_URL": URL,
            "SENIOR_API_KEY": "not-needed-for-mock",
        },
    ],
)
def test_misconfigured_senior_fails_at_start_up(env):
    with pytest.raises(RuntimeError):
        settings(**env)
