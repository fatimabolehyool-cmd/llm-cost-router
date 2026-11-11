"""Model adapters. One class per provider protocol; the orchestrator never builds HTTP itself.

Model output is UNTRUSTED: adapters return raw text only; it is normalized to a closed option
set before use, never executed, and never stored raw (it can echo the user's personal data).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx


class ModelUnavailable(Exception):
    """Timeout, connection error, 5xx or an open circuit breaker."""


@dataclass
class ModelReply:
    text: str
    input_tokens: int
    output_tokens: int  # for the Anthropic senior this includes thinking tokens (billed as output)
    latency_ms: float
    stop_reason: str | None = None


class CircuitBreaker:
    """Opens after `threshold` consecutive failures; one trial call after `cooldown_s`.

    State is per router process (documented limitation if several replicas run).
    """

    def __init__(self, threshold: int = 5, cooldown_s: float = 60.0) -> None:
        self.threshold, self.cooldown_s = threshold, cooldown_s
        self.failures, self.opened_at = 0, 0.0

    def allow(self) -> bool:
        if self.failures < self.threshold:
            return True
        return time.monotonic() - self.opened_at >= self.cooldown_s  # half-open trial

    def record(self, ok: bool) -> None:
        if ok:
            self.failures = 0
        else:
            self.failures += 1
            if self.failures >= self.threshold:
                self.opened_at = time.monotonic()


class OllamaJunior:
    """Calls Ollama's POST /api/chat (also served by the mock server in tests)."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        model: str,
        timeout_s: float,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self.client, self.base_url, self.model, self.timeout_s = (
            client,
            base_url.rstrip("/"),
            model,
            timeout_s,
        )
        self.breaker = breaker or CircuitBreaker()

    async def ask(self, system: str, user: str, max_tokens: int) -> ModelReply:
        if not self.breaker.allow():
            raise ModelUnavailable("circuit_open")
        body = {
            "model": self.model,
            "stream": False,
            "keep_alive": "30m",
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"temperature": 0, "seed": 42, "num_predict": max_tokens, "num_ctx": 4096},
            "think": False,
        }
        t0 = time.perf_counter()
        try:
            r = await self.client.post(
                f"{self.base_url}/api/chat", json=body, timeout=self.timeout_s
            )
            r.raise_for_status()
            j = r.json()
        except (httpx.HTTPError, ValueError) as e:
            self.breaker.record(False)
            raise ModelUnavailable(type(e).__name__) from None  # no payload in the message
        self.breaker.record(True)
        return ModelReply(
            text=j.get("message", {}).get("content", ""),
            input_tokens=int(j.get("prompt_eval_count") or 0),
            output_tokens=int(j.get("eval_count") or 0),
            latency_ms=(time.perf_counter() - t0) * 1000,
        )


class HttpSenior:
    """Provider-neutral senior adapter used with the mock provider.

    The real provider adapter (chosen with ADR-14) implements the same `ask` signature.
    It must only ever be called by EgressGateway.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        url: str,
        model: str,
        api_key: str,
        timeout_s: float,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self.client, self.url, self.model, self.api_key, self.timeout_s = (
            client,
            url,
            model,
            api_key,
            timeout_s,
        )
        self.breaker = breaker or CircuitBreaker()

    def output_budget(self, max_tokens: int) -> int:
        """Most output tokens one call can be billed for (the egress reservation uses this)."""
        return max_tokens

    async def ask(self, system: str, user: str, max_tokens: int) -> ModelReply:
        if not self.breaker.allow():
            raise ModelUnavailable("circuit_open")
        t0 = time.perf_counter()
        try:
            r = await self.client.post(
                self.url,
                timeout=self.timeout_s,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "system": system,
                    "user": user,
                    "max_tokens": max_tokens,
                },
            )
            r.raise_for_status()
            j = r.json()
        except (httpx.HTTPError, ValueError) as e:
            self.breaker.record(False)
            raise ModelUnavailable(type(e).__name__) from None
        self.breaker.record(True)
        return ModelReply(
            text=j.get("text", ""),
            input_tokens=int(j.get("input_tokens") or 0),
            output_tokens=int(j.get("output_tokens") or 0),
            latency_ms=(time.perf_counter() - t0) * 1000,
        )


class AnthropicSenior:
    """Claude Messages API adapter (interface I4). Only EgressGateway may call it.

    Implementation-time changes to the I4 contract, from the API documentation (Nov 2026):
    * Claude Opus 5.5 always thinks before answering and `max_tokens` covers thinking plus text,
      so the caller's 32-token answer budget gets `thinking_headroom` extra tokens, and thinking
      depth is lowered with `output_config.effort` instead of being switched off.
    * Sampling parameters (temperature) are not accepted, so senior answers are not
      deterministic; this is a stated limitation of the evaluation.
    * Auth uses `Authorization: Bearer` (current) rather than the legacy `x-api-key` header.

    Retries: one retry after `retry_delay_s` on 429, 500 or 529, or when the connection could not
    be made (nothing was processed or billed). Never on other 4xx, and never after a read
    timeout, because the request may already have been processed and billed.
    """

    RETRY_STATUSES = frozenset({429, 500, 529})

    def __init__(
        self,
        client: httpx.AsyncClient,
        url: str,
        model: str,
        api_key: str,
        timeout_s: float,
        api_version: str = "2026-10-21",
        effort: str | None = "low",
        thinking: dict | None = None,
        thinking_headroom: int = 1024,
        retry_delay_s: float = 1.0,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self.client, self.url, self.model, self.timeout_s = client, url, model, timeout_s
        self._headers = {
            "authorization": f"Bearer {api_key}",
            "anthropic-version": api_version,
            "content-type": "application/json",
        }
        self.effort, self.thinking = effort, thinking
        self.thinking_headroom, self.retry_delay_s = thinking_headroom, retry_delay_s
        self.breaker = breaker or CircuitBreaker()

    def __repr__(self) -> str:  # never show the key, even in a debugger or a traceback
        return f"AnthropicSenior(model={self.model!r}, url={self.url!r})"

    def output_budget(self, max_tokens: int) -> int:
        return max_tokens + self.thinking_headroom

    def _body(self, system: str, user: str, max_tokens: int) -> dict:
        body: dict = {
            "model": self.model,
            "max_tokens": self.output_budget(max_tokens),
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if self.effort:
            body["output_config"] = {"effort": self.effort}
        if self.thinking:
            body["thinking"] = self.thinking
        return body

    async def _post_once(self, body: dict) -> httpx.Response:
        return await self.client.post(
            self.url, json=body, headers=self._headers, timeout=self.timeout_s
        )

    async def ask(self, system: str, user: str, max_tokens: int) -> ModelReply:
        if not self.breaker.allow():
            raise ModelUnavailable("circuit_open")
        body = self._body(system, user, max_tokens)
        t0 = time.perf_counter()
        try:
            for attempt in (1, 2):
                try:
                    r = await self._post_once(body)
                except httpx.ConnectError:
                    if attempt == 2:
                        raise
                else:
                    if r.status_code not in self.RETRY_STATUSES or attempt == 2:
                        break
                await asyncio.sleep(self.retry_delay_s)
            if r.status_code != 200:
                raise ModelUnavailable(f"http_{r.status_code}")  # status only, never the body
            j = r.json()
        except ModelUnavailable:
            self.breaker.record(False)
            raise
        except (httpx.HTTPError, ValueError) as e:
            self.breaker.record(False)
            raise ModelUnavailable(type(e).__name__) from None
        self.breaker.record(True)
        text = "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")
        usage = j.get("usage") or {}
        return ModelReply(
            text=text,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            latency_ms=(time.perf_counter() - t0) * 1000,
            stop_reason=j.get("stop_reason"),
        )
