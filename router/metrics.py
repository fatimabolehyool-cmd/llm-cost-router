"""Prometheus metrics for the router (interface I10, architecture section H).

Metrics live in their own registry so tests can create the app more than once. Request-level
metrics are recorded by the API layer from each Outcome; process-level counters the core keeps
anyway (policy fallbacks, egress refusals, dropped log rows, breaker state) are read at scrape
time by RouterCollector, so the orchestrator and gateway stay free of monitoring code.

Names start with `router_`. Labels are bounded sets (modes, statuses, reasons, data classes,
paths, providers); no request content and no free text ever becomes a label.
"""

from __future__ import annotations

from collections.abc import Iterable

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric

REGISTRY = CollectorRegistry(auto_describe=True)

REQUESTS = Counter(
    "router_requests",
    "Answered or abstained requests, by outcome",
    ["mode", "status", "reason", "data_class"],
    registry=REGISTRY,
)
REJECTED = Counter(
    "router_rejected_requests",
    "Requests refused before any model answer (HTTP 503), by error code",
    ["code"],
    registry=REGISTRY,
)
SPEND = Counter("router_spend_usd", "Paid senior cost in USD", registry=REGISTRY)
LATENCY = Histogram(
    "router_request_latency_seconds",
    "End-to-end request latency inside the router, by path",
    ["path"],
    buckets=(0.25, 0.5, 1, 1.5, 2, 3, 4, 6, 8, 12, 20, 30),
    registry=REGISTRY,
)
POLICY_P = Histogram(
    "router_policy_probability",
    "P(junior answer correct) returned by the policy; a shift signals model or data drift",
    buckets=tuple(i / 10 for i in range(1, 11)),
    registry=REGISTRY,
)
SPEND_CAP = Gauge("router_spend_cap_usd", "Configured global spend cap", registry=REGISTRY)
POLICY_RELOADS = Counter(
    "router_policy_reloads",
    "Admin policy reloads (interface I7) by result; a failed reload keeps the old policy",
    ["result"],
    registry=REGISTRY,
)
FEEDBACK = Counter(
    "router_feedback",
    "Caller feedback received (interface I2), by flag",
    ["flag"],
    registry=REGISTRY,
)


# Every outcome the orchestrator can produce (tests/test_monitoring.py checks this list against
# router/orchestrator.py). Series are created at 0 on start-up, because Prometheus derives rates
# from the change between scrapes: a series that only appears with its first request makes that
# request invisible to rate() and increase(), which matters at demo-sized traffic.
ANSWERED_REASONS = (
    "juniors_agreed",
    "policy_accepted",
    "cheapest_unverified",
    "escalated_disagreement",
    "escalated_low_confidence",
    "best_quality_mode",
)
ABSTAINED_REASONS = (
    "sensitive_disagreement",
    "sensitive_low_confidence",
    "juniors_unreadable",
    "egress_blocked",
    "budget_exceeded",
    "senior_unavailable",
    "senior_unreadable",
)
MODES = ("cheapest", "balanced", "best_quality")
DATA_CLASSES = ("non_sensitive", "sensitive")
PATHS = ("juniors", "escalated", "best_quality", "abstained")
REJECT_CODES = ("overloaded", "models_unavailable", "database_unavailable")
FEEDBACK_FLAGS = ("wrong", "correct", "unsure")
RELOAD_RESULTS = ("ok", "failed")


def initialize_series() -> None:
    """Create every known label combination at 0 (idempotent)."""
    for mode in MODES:
        for data_class in DATA_CLASSES:
            for reason in ANSWERED_REASONS:
                REQUESTS.labels(mode, "answered", reason, data_class)
            for reason in ABSTAINED_REASONS:
                REQUESTS.labels(mode, "abstained", reason, data_class)
    for path in PATHS:
        LATENCY.labels(path)
    for code in REJECT_CODES:
        REJECTED.labels(code)
    for flag in FEEDBACK_FLAGS:
        FEEDBACK.labels(flag)
    for result in RELOAD_RESULTS:
        POLICY_RELOADS.labels(result)


def path_of(status: str, escalated: bool, reason: str) -> str:
    """The latency path a request took (a bounded label)."""
    if status == "abstained":
        return "abstained"
    if reason == "best_quality_mode":
        return "best_quality"
    return "escalated" if escalated else "juniors"


def record_outcome(outcome, mode: str, policy_p: float | None = None) -> None:
    """Record one finished request (an orchestrator Outcome)."""
    REQUESTS.labels(mode, outcome.status, outcome.reason, outcome.data_class).inc()
    if outcome.cost_usd:
        SPEND.inc(outcome.cost_usd)
    LATENCY.labels(path_of(outcome.status, outcome.escalated, outcome.reason)).observe(
        outcome.latency_ms / 1000
    )
    if policy_p is not None:
        POLICY_P.observe(policy_p)


class RouterCollector:
    """Reads counters and state the core already keeps, at scrape time."""

    def __init__(self, orch, gateway, store) -> None:
        self.orch, self.gateway, self.store = orch, gateway, store

    def describe(self) -> Iterable[Metric]:
        """Nothing up front: the families are produced in collect() (an unchecked
        collector)."""
        return []  # unchecked collector: families are produced only in collect()

    def collect(self) -> Iterable[Metric]:
        """Values read from the live objects at scrape time: the policy version
        serving, policy fallbacks, blocked egress calls, dropped log rows, audits and skipped audits,
        and open circuit breakers."""
        info = GaugeMetricFamily(
            "router_policy_info",
            "The routing policy version serving now (rule-v0 when no trained model is loaded)",
            labels=["version"],
        )
        info.add_metric([self.orch.versions["policy"]], 1.0)
        yield info
        yield CounterMetricFamily(
            "router_policy_fallbacks",
            "Balanced-mode decisions made by rule-v0 because the policy was unavailable",
            value=self.orch.policy_fallbacks,
        )
        yield CounterMetricFamily(
            "router_egress_blocked",
            "External calls refused by the egress gateway because the data was sensitive",
            value=self.gateway.blocked_count,
        )
        yield CounterMetricFamily(
            "router_log_dropped",
            "Routing records dropped (queue full or database write error)",
            value=self.store.dropped,
        )
        audit = getattr(self.orch, "audit", None)
        if audit is not None:
            done = CounterMetricFamily(
                "router_audits", "Completed audits by result", labels=["result"]
            )
            for result in ("agree", "disagree", "unreadable"):
                done.add_metric([result], audit.results[result])
            yield done
            skipped = CounterMetricFamily(
                "router_audits_skipped", "Audits not run, by reason", labels=["reason"]
            )
            for reason in ("backlog_full", "egress_blocked", "budget", "senior_down", "error"):
                skipped.add_metric([reason], audit.skipped[reason])
            yield skipped
        breakers = GaugeMetricFamily(
            "router_breaker_open",
            "1 if the provider's circuit breaker is open",
            labels=["provider"],
        )
        for name, model in (
            ("junior1", self.orch.j1),
            ("junior2", self.orch.j2),
            ("senior", self.gateway.senior),
        ):
            b = getattr(model, "breaker", None)
            if b is not None:
                breakers.add_metric([name], float(not b.allow()))
        yield breakers
