"""Egress gateway (ADR-4): the ONLY code path that may send data to an external provider.

Two checks happen here, in this order, for every external call:
  1. data class must be non-sensitive, otherwise EgressBlocked (and a counter increments);
  2. the estimated cost is reserved atomically against the global spend cap (FR-8).
Because every external call goes through this class, "sensitive data never leaves" is
testable in one place (canary test S3) instead of being scattered through the code.
"""

from __future__ import annotations

from .adapters import HttpSenior, ModelReply
from .privacy import NON_SENSITIVE
from .store import Store


class EgressBlocked(Exception):
    pass


class BudgetExceeded(Exception):
    pass


class EgressGateway:
    def __init__(self, senior: HttpSenior, store: Store, price_in: float, price_out: float) -> None:
        self.senior, self.store = senior, store
        self.price_in, self.price_out = price_in, price_out  # USD per million tokens
        self.blocked_count = 0

    def estimate_usd(self, prompt_chars: int, max_tokens: int) -> float:
        return (prompt_chars / 4 * self.price_in + max_tokens * self.price_out) / 1e6

    def actual_usd(self, reply: ModelReply) -> float:
        return (reply.input_tokens * self.price_in + reply.output_tokens * self.price_out) / 1e6

    async def ask_senior(
        self, data_class: str, system: str, user: str, max_tokens: int
    ) -> tuple[ModelReply, float]:
        if data_class != NON_SENSITIVE:
            self.blocked_count += 1
            raise EgressBlocked("sensitive data may not leave the system")
        estimate = self.estimate_usd(len(system) + len(user), max_tokens)
        if not await self.store.reserve_spend(estimate):
            raise BudgetExceeded("global spend cap reached")
        try:
            reply = await self.senior.ask(system, user, max_tokens)
        except Exception:
            await self.store.settle_spend(estimate, 0.0)
            raise
        cost = self.actual_usd(reply)
        await self.store.settle_spend(estimate, cost)
        return reply, cost
