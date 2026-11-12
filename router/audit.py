"""Audit sampler (ADR-26, FR-16; architecture feedback A1).

A random share (AUDIT_RATE, default 5%) of ACCEPTED junior answers is re-asked to the senior
after the response has been sent, so the live false-acceptance rate can be estimated without
waiting for user feedback. Rules:

* Only answered, verified, non-escalated, NON-SENSITIVE requests are eligible. The call goes
  through the egress gateway, which refuses sensitive data a second time (defence in depth);
  the canary test S3 runs with AUDIT_RATE=1 to prove no sensitive token reaches the senior.
* It never blocks or changes a response: it runs as a background task after the answer is
  returned, with bounded concurrency and a bounded backlog; anything that goes wrong (budget,
  senior down, backlog full) is counted as a skip, never raised.
* The senior is not ground truth: a disagreement goes to the human review queue
  (view audit_review_queue), it does not overwrite the answer or the label.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections import Counter

from common.normalizer import normalize

from .adapters import ModelUnavailable
from .egress import BudgetExceeded, EgressBlocked, EgressGateway
from .privacy import NON_SENSITIVE

log = logging.getLogger("router.audit")


class AuditSampler:
    def __init__(
        self,
        gateway: EgressGateway,
        store,
        rate: float = 0.05,
        max_pending: int = 50,
        concurrency: int = 2,
        rng: random.Random | None = None,
    ) -> None:
        if not 0.0 <= rate <= 1.0:
            raise ValueError("audit rate must be in [0, 1]")
        self.gateway, self.store, self.rate, self.max_pending = gateway, store, rate, max_pending
        self.rng = rng or random.Random()
        self.sem = asyncio.Semaphore(concurrency)
        self.pending: set[asyncio.Task] = set()
        self.results: Counter = Counter()  # agree / disagree / unreadable
        self.skipped: Counter = Counter()  # backlog_full / egress_blocked / budget / senior_down

    def eligible(self, outcome) -> bool:
        return (
            outcome.status == "answered"
            and outcome.verified
            and not outcome.escalated
            and outcome.data_class == NON_SENSITIVE
        )

    def maybe_schedule(self, outcome, task_type, system, user, max_tokens, labels) -> bool:
        """Called after the response is built. Returns True if an audit was scheduled."""
        if not self.eligible(outcome) or self.rng.random() >= self.rate:
            return False
        if len(self.pending) >= self.max_pending:
            self.skipped["backlog_full"] += 1
            return False
        task = asyncio.create_task(
            self._audit(outcome, task_type, system, user, max_tokens, labels)
        )
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)
        return True

    async def _audit(self, outcome, task_type, system, user, max_tokens, labels) -> None:
        async with self.sem:
            try:
                reply, cost = await self.gateway.ask_senior(
                    outcome.data_class, system, user, max_tokens
                )
            except EgressBlocked:
                self.skipped["egress_blocked"] += 1
                return
            except BudgetExceeded:
                self.skipped["budget"] += 1
                return
            except ModelUnavailable:
                self.skipped["senior_down"] += 1
                return
            except Exception as e:  # noqa: BLE001 - an audit must never crash the router
                self.skipped["error"] += 1
                log.warning("audit failed: %s", type(e).__name__)
                return
        senior_answer = normalize(task_type, reply.text, labels)
        if senior_answer is None:
            self.results["unreadable"] += 1
            agrees = None
        else:
            agrees = senior_answer == outcome.answer
            self.results["agree" if agrees else "disagree"] += 1
        self.store.enqueue_audit(
            {
                "request_id": outcome.request_id,
                "audit_answer": senior_answer,  # a normalized option, never raw model text
                "audit_agrees": agrees,
                "audit_cost_usd": cost,
            }
        )

    async def drain(self, timeout_s: float = 10.0) -> None:
        """Wait for running audits (used at shutdown and in tests)."""
        if self.pending:
            await asyncio.wait(set(self.pending), timeout=timeout_s)
