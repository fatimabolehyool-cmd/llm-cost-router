"""Cascade orchestrator: modes, parallel juniors, the policy decision, escalation, abstention.

Decision rules (FR-3, FR-4, FR-11, ADR-3, ADR-9). `rule-v0` is the agreement rule:
  agree                         -> accept the junior answer (verified)
  disagree, sensitive           -> ABSTAIN (never escalate: sensitive data stays local)
  disagree, mode=cheapest       -> junior answer marked unverified (abstain if none readable)
  disagree, mode=balanced       -> escalate to the senior via the egress gateway
  mode=best_quality, non-sens.  -> senior directly (juniors skipped)
  senior fails / unreadable     -> ABSTAIN (no silent unverified answer)
  both juniors unavailable      -> 503 (JuniorsUnavailable), never a paid escalation
  junior concurrency cap full   -> 503 (Overloaded), never a paid escalation (ADR-13)
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field

from common.normalizer import normalize

from .adapters import ModelUnavailable, OllamaJunior
from .config import Settings
from .egress import BudgetExceeded, EgressBlocked, EgressGateway
from .privacy import SENSITIVE, classify, keyed_hash, mask
from .prompts import build_prompt, load_prompts
from .store import Store


class Overloaded(Exception):
    pass


class JuniorsUnavailable(Exception):
    pass


@dataclass
class Outcome:
    request_id: str
    status: str  # answered | abstained
    answer: str | None
    verified: bool
    escalated: bool
    model: str | None
    reason: str
    data_class: str
    cost_usd: float = 0.0
    latency_ms: int = 0
    versions: dict = field(default_factory=dict)


class Orchestrator:
    def __init__(
        self, s: Settings, j1: OllamaJunior, j2: OllamaJunior, gateway: EgressGateway, store: Store
    ):
        self.s, self.j1, self.j2, self.gateway, self.store = s, j1, j2, gateway, store
        self.prompts = load_prompts(s.prompts_file)
        self.cap = asyncio.Semaphore(s.junior_concurrency_cap)
        self.versions = {
            "api": s.api_version,
            "policy": s.policy_version,
            "prompt": self.prompts["version"],
            "junior1": s.junior1_model,
            "junior2": s.junior2_model,
            "senior": s.senior_model,
        }

    async def answer(
        self, question: str, task_type: str, options: list[str], mode: str, sensitive: bool
    ) -> Outcome:
        t0 = time.perf_counter()
        rid = f"req_{uuid.uuid4().hex[:12]}"
        data_class = classify(question + "\n" + "\n".join(options), sensitive)
        labels = options  # option texts (choice) or category names (label), for the normalizer
        system, user, max_tokens = build_prompt(self.prompts, task_type, question, options)

        a1 = a2 = None
        agreed = None
        if mode == "best_quality" and data_class != SENSITIVE:
            out = await self._senior(
                rid,
                data_class,
                system,
                user,
                max_tokens,
                task_type,
                labels,
                reason_ok="best_quality_mode",
            )
        else:
            a1, a2 = await self._juniors(
                system, user, max_tokens, task_type, labels
            )  # may raise 503s
            agreed = a1 is not None and a1 == a2
            if agreed:
                out = self._outcome(
                    rid,
                    "answered",
                    a1,
                    True,
                    False,
                    self.s.junior1_model,
                    "juniors_agreed",
                    data_class,
                )
            elif data_class == SENSITIVE:
                out = self._outcome(
                    rid, "abstained", None, False, False, None, "sensitive_disagreement", data_class
                )
            elif mode == "cheapest":
                pick = a1 if a1 is not None else a2
                model = self.s.junior1_model if a1 is not None else self.s.junior2_model
                out = (
                    self._outcome(
                        rid,
                        "answered",
                        pick,
                        False,
                        False,
                        model,
                        "cheapest_unverified",
                        data_class,
                    )
                    if pick is not None
                    else self._outcome(
                        rid, "abstained", None, False, False, None, "juniors_unreadable", data_class
                    )
                )
            else:
                out = await self._senior(
                    rid,
                    data_class,
                    system,
                    user,
                    max_tokens,
                    task_type,
                    labels,
                    reason_ok="escalated_disagreement",
                )
        out.latency_ms = int((time.perf_counter() - t0) * 1000)
        out.versions = self.versions
        self._log(out, question, task_type, mode, len(options), a1, a2, agreed)
        return out

    async def _juniors(self, system, user, max_tokens, task_type, labels):
        if self.cap.locked():
            raise Overloaded()
        async with self.cap:
            r1, r2 = await asyncio.gather(
                self.j1.ask(system, user, max_tokens),
                self.j2.ask(system, user, max_tokens),
                return_exceptions=True,
            )
        if isinstance(r1, ModelUnavailable) and isinstance(r2, ModelUnavailable):
            raise JuniorsUnavailable()
        for r in (r1, r2):
            if isinstance(r, BaseException) and not isinstance(r, ModelUnavailable):
                raise r
        a1 = None if isinstance(r1, BaseException) else normalize(task_type, r1.text, labels)
        a2 = None if isinstance(r2, BaseException) else normalize(task_type, r2.text, labels)
        return a1, a2

    async def _senior(
        self, rid, data_class, system, user, max_tokens, task_type, labels, reason_ok
    ):
        try:
            reply, cost = await self.gateway.ask_senior(data_class, system, user, max_tokens)
        except EgressBlocked:
            return self._outcome(
                rid, "abstained", None, False, False, None, "egress_blocked", data_class
            )
        except BudgetExceeded:
            return self._outcome(
                rid, "abstained", None, False, False, None, "budget_exceeded", data_class
            )
        except ModelUnavailable:
            return self._outcome(
                rid, "abstained", None, False, True, None, "senior_unavailable", data_class
            )
        ans = normalize(task_type, reply.text, labels)
        if ans is None:
            return self._outcome(
                rid, "abstained", None, False, True, None, "senior_unreadable", data_class, cost
            )
        return self._outcome(
            rid, "answered", ans, True, True, self.s.senior_model, reason_ok, data_class, cost
        )

    @staticmethod
    def _outcome(
        rid, status, answer, verified, escalated, model, reason, data_class, cost=0.0
    ) -> Outcome:
        return Outcome(rid, status, answer, verified, escalated, model, reason, data_class, cost)

    def _log(self, o: Outcome, question, task_type, mode, n_options, a1, a2, agreed) -> None:
        self.store.enqueue_log(
            {
                "request_id": o.request_id,
                "task_type": task_type,
                "mode": mode,
                "data_class": o.data_class,
                "prompt_hash": keyed_hash(question, self.s.hash_key),
                "prompt_masked": None if o.data_class == SENSITIVE else mask(question),
                "n_options": n_options,
                "j1_answer": a1,
                "j2_answer": a2,
                "agreed": agreed,
                "escalated": o.escalated,
                "status": o.status,
                "answer": o.answer,
                "verified": o.verified,
                "final_model": o.model,
                "reason": o.reason,
                "policy_version": self.s.policy_version,
                "prompt_version": self.versions["prompt"],
                "junior1_model": self.s.junior1_model,
                "junior2_model": self.s.junior2_model,
                "senior_model": self.s.senior_model,
                "cost_usd": o.cost_usd,
                "latency_ms": o.latency_ms,
            }
        )
