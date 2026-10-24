"""Model adapters. One class per provider protocol; the orchestrator never builds HTTP itself.

Model output is UNTRUSTED: adapters return raw text only; it is normalized to a closed option
set before use, never executed, and never stored raw (it can echo the user's personal data).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx


class ModelUnavailable(Exception):
    """Timeout, connection error, 5xx or an open circuit breaker."""


@dataclass
class ModelReply:
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


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

    def __init__(self, client: httpx.AsyncClient, base_url: str, model: str, timeout_s: float,
                 breaker: CircuitBreaker | None = None) -> None:
        self.client, self.base_url, self.model, self.timeout_s = client, base_url.rstrip("/"), model, timeout_s
        self.breaker = breaker or CircuitBreaker()

    async def ask(self, system: str, user: str, max_tokens: int) -> ModelReply:
        if not self.breaker.allow():
            raise ModelUnavailable("circuit_open")
        body = {
            "model": self.model, "stream": False, "keep_alive": "30m",
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"temperature": 0, "seed": 42, "num_predict": max_tokens, "num_ctx": 4096},
            "think": False,
        }
        t0 = time.perf_counter()
        try:
            r = await self.client.post(f"{self.base_url}/api/chat", json=body, timeout=self.timeout_s)
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

    def __init__(self, client: httpx.AsyncClient, url: str, model: str, api_key: str, timeout_s: float,
                 breaker: CircuitBreaker | None = None) -> None:
        self.client, self.url, self.model, self.api_key, self.timeout_s = client, url, model, api_key, timeout_s
        self.breaker = breaker or CircuitBreaker()

    async def ask(self, system: str, user: str, max_tokens: int) -> ModelReply:
        if not self.breaker.allow():
            raise ModelUnavailable("circuit_open")
        t0 = time.perf_counter()
        try:
            r = await self.client.post(
                self.url, timeout=self.timeout_s,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "system": system, "user": user, "max_tokens": max_tokens},
            )
            r.raise_for_status()
            j = r.json()
        except (httpx.HTTPError, ValueError) as e:
            self.breaker.record(False)
            raise ModelUnavailable(type(e).__name__) from None
        self.breaker.record(True)
        return ModelReply(text=j.get("text", ""), input_tokens=int(j.get("input_tokens") or 0),
                          output_tokens=int(j.get("output_tokens") or 0),
                          latency_ms=(time.perf_counter() - t0) * 1000)
