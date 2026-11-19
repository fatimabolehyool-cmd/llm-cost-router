"""Egress gateway (ADR-4): the ONLY code path that may send data to an external provider.

Two checks happen here, in this order, for every external call:
  1. data class must be non-sensitive, otherwise EgressBlocked (and a counter increments);
  2. the estimated cost is reserved atomically against the global spend cap (FR-8).
Because every external call goes through this class, "sensitive data never leaves" is
testable in one place (canary test S3) instead of being scattered through the code.
"""

from __future__ import annotations

from typing import Protocol

from .adapters import ModelReply
from .privacy import NON_SENSITIVE
from .store import Store


class SeniorModel(Protocol):
    """What the gateway needs from a senior adapter (HttpSenior mock, AnthropicSenior)."""

    def output_budget(self, max_tokens: int) -> int:
        """Most output tokens one call can be billed for (used to reserve spend)."""

    async def ask(self, system: str, user: str, max_tokens: int) -> ModelReply:
        """Ask the senior model; raises ModelUnavailable on failure."""


class EgressBlocked(Exception):
    """A request that is not non-sensitive reached the gateway; it is refused and counted."""


class BudgetExceeded(Exception):
    """The global spend cap has no room for this call's worst-case cost."""


class EgressGateway:
    """The only path to an external provider (ADR-4).

    It refuses anything not classified non-sensitive, reserves the worst-case cost in PostgreSQL
    before calling the senior, and settles the actual cost afterwards."""

    def __init__(
        self,
        senior: SeniorModel,
        store: Store,
        price_in: float,
        price_out: float,
    ) -> None:
        self.senior, self.store = senior, store
        self.price_in, self.price_out = price_in, price_out  # USD per million tokens
        self.blocked_count = 0

    def estimate_usd(self, prompt_chars: int, max_tokens: int) -> float:
        """Worst-case cost in USD: about 4 characters per input token, plus every
        output token the call may be billed for."""
        return (prompt_chars / 4 * self.price_in + max_tokens * self.price_out) / 1e6

    def actual_usd(self, reply: ModelReply) -> float:
        """Cost in USD from the token counts the provider reported."""
        return (reply.input_tokens * self.price_in + reply.output_tokens * self.price_out) / 1e6

    async def ask_senior(
        self, data_class: str, system: str, user: str, max_tokens: int
    ) -> tuple[ModelReply, float]:
        """Send one non-sensitive prompt to the senior; return (reply, cost in USD).

        Raises EgressBlocked for sensitive data and BudgetExceeded when the cap has no room. A model
        error releases the reservation and is raised again."""
        if data_class != NON_SENSITIVE:
            self.blocked_count += 1
            raise EgressBlocked("sensitive data may not leave the system")
        # Reserve the worst case: every token the adapter may be billed for (thinking included).
        estimate = self.estimate_usd(len(system) + len(user), self.senior.output_budget(max_tokens))
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
