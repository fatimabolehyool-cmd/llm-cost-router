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

With a trained policy (balanced mode only): accept the junior pick when P(correct) >= the
model's threshold, otherwise escalate (abstain if sensitive). If the policy is unavailable the
agreement rule decides (rule-v0 fallback), so a policy failure changes cost, never safety.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

from common.normalizer import normalize

from .adapters import ModelUnavailable, OllamaJunior
from .audit import AuditSampler
from .config import Settings
from .egress import BudgetExceeded, EgressBlocked, EgressGateway
from .policy import PolicyClient, PolicyUnavailable
from .privacy import SENSITIVE, classify, keyed_hash, mask
from .prompts import build_prompt, load_prompts
from .store import Store

log = logging.getLogger("router.orchestrator")


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
    policy_p: float | None = None  # P(junior answer correct) when the trained policy decided


class Orchestrator:
    def __init__(
        self,
        s: Settings,
        j1: OllamaJunior,
        j2: OllamaJunior,
        gateway: EgressGateway,
        store: Store,
        policy: PolicyClient | None = None,
        audit: AuditSampler | None = None,
    ):
        self.s, self.j1, self.j2, self.gateway, self.store = s, j1, j2, gateway, store
        self.policy, self.audit = policy, audit
        self.policy_fallbacks = 0  # exported as a metric
        self.prompts = load_prompts(s.prompts_file)
        self.cap = asyncio.Semaphore(s.junior_concurrency_cap)
        self.versions = {
            "api": s.api_version,
            "policy": policy.meta.model_version if policy else s.policy_version,
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
        decided_by, policy_p = self.s.policy_version, None
        if mode == "best_quality" and data_class != SENSITIVE:
            decided_by = "not_used"  # best quality always asks the senior
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
            a1, a2, usage = await self._juniors(
                system, user, max_tokens, task_type, labels
            )  # may raise 503s
            agreed = a1 is not None and a1 == a2
            pick = a1 if a1 is not None else a2
            pick_model = self.s.junior1_model if a1 is not None else self.s.junior2_model
            accept, decided_by = agreed, self.s.policy_version
            if mode == "balanced" and self.policy is not None and pick is not None:
                accept, decided_by, policy_p = await self._policy_decision(
                    task_type, len(options), a1, a2, usage, agreed
                )
            by_model = decided_by not in (self.s.policy_version, "rule-v0-fallback")
            if accept and pick is not None:
                reason = "policy_accepted" if by_model else "juniors_agreed"
                out = self._outcome(
                    rid, "answered", pick, True, False, pick_model, reason, data_class
                )
            elif data_class == SENSITIVE:
                reason = "sensitive_low_confidence" if by_model else "sensitive_disagreement"
                out = self._outcome(rid, "abstained", None, False, False, None, reason, data_class)
            elif mode == "cheapest":
                out = (
                    self._outcome(
                        rid,
                        "answered",
                        pick,
                        False,
                        False,
                        pick_model,
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
                    reason_ok="escalated_low_confidence" if by_model else "escalated_disagreement",
                )
        out.latency_ms = int((time.perf_counter() - t0) * 1000)
        out.versions = {**self.versions, "policy": decided_by}
        out.policy_p = policy_p
        self._log(out, question, task_type, mode, len(options), a1, a2, agreed, policy_p)
        if self.audit is not None:  # after the row is queued; runs in the background
            self.audit.maybe_schedule(out, task_type, system, user, max_tokens, labels)
        return out

    async def _policy_decision(self, task_type, n_options, a1, a2, usage, agreed):
        """Return (accept, decided_by, probability). Falls back to the agreement rule."""
        x = self.policy.meta.vector(
            task_type,
            n_options,
            a1,
            a2,
            usage["prompt_tokens"],
            usage["j1_tokens"],
            usage["j2_tokens"],
        )
        try:
            p = await self.policy.p_correct(x)
        except PolicyUnavailable as e:
            self.policy_fallbacks += 1
            log.warning("policy unavailable (%s); rule-v0 decides", e)
            return agreed, "rule-v0-fallback", None
        return p >= self.policy.meta.threshold_balanced, self.policy.meta.model_version, p

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
        ok1, ok2 = not isinstance(r1, BaseException), not isinstance(r2, BaseException)
        usage = {  # the token counts the policy features use (same fields as the replay pool)
            "prompt_tokens": (r1.input_tokens if ok1 else 0) or (r2.input_tokens if ok2 else 0),
            "j1_tokens": r1.output_tokens if ok1 else 0,
            "j2_tokens": r2.output_tokens if ok2 else 0,
        }
        return a1, a2, usage

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

    def _log(
        self, o: Outcome, question, task_type, mode, n_options, a1, a2, agreed, policy_p=None
    ) -> None:
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
                "policy_version": o.versions["policy"],
                "policy_p": policy_p,
                "prompt_version": self.versions["prompt"],
                "junior1_model": self.s.junior1_model,
                "junior2_model": self.s.junior2_model,
                "senior_model": self.s.senior_model,
                "cost_usd": o.cost_usd,
                "latency_ms": o.latency_ms,
            }
        )
