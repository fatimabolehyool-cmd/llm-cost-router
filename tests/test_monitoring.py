"""Prometheus metrics (interface I10), the dashboard and the alert rules stay consistent."""

import json
import re
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from router import metrics
from router.adapters import CircuitBreaker
from router.app import app

ROOT = Path(__file__).resolve().parents[1]


def outcome(status="answered", reason="juniors_agreed", escalated=False, cost=0.0, ms=1500):
    return SimpleNamespace(
        status=status,
        reason=reason,
        escalated=escalated,
        data_class="non_sensitive",
        cost_usd=cost,
        latency_ms=ms,
    )


def sample(name, labels=None):
    return metrics.REGISTRY.get_sample_value(name, labels or {}) or 0.0


def test_record_outcome_counts_requests_spend_latency_and_probability():
    labels = {
        "mode": "balanced",
        "status": "answered",
        "reason": "escalated_low_confidence",
        "data_class": "non_sensitive",
    }
    before = (
        sample("router_requests_total", labels),
        sample("router_spend_usd_total"),
        sample("router_request_latency_seconds_count", {"path": "escalated"}),
        sample("router_policy_probability_count"),
    )
    metrics.record_outcome(
        outcome(reason="escalated_low_confidence", escalated=True, cost=0.0027),
        "balanced",
        policy_p=0.31,
    )
    assert sample("router_requests_total", labels) == before[0] + 1
    assert abs(sample("router_spend_usd_total") - before[1] - 0.0027) < 1e-9
    assert sample("router_request_latency_seconds_count", {"path": "escalated"}) == before[2] + 1
    assert sample("router_policy_probability_count") == before[3] + 1


def test_latency_paths_are_a_small_fixed_set():
    assert metrics.path_of("abstained", False, "sensitive_low_confidence") == "abstained"
    assert metrics.path_of("answered", True, "best_quality_mode") == "best_quality"
    assert metrics.path_of("answered", True, "escalated_disagreement") == "escalated"
    assert metrics.path_of("answered", False, "policy_accepted") == "juniors"


def test_collector_reads_state_the_core_already_keeps():
    open_breaker = CircuitBreaker(threshold=1, cooldown_s=60)
    open_breaker.record(False)
    orch = SimpleNamespace(
        policy_fallbacks=3,
        j1=SimpleNamespace(breaker=open_breaker),
        j2=SimpleNamespace(breaker=CircuitBreaker()),
    )
    gateway = SimpleNamespace(blocked_count=5, senior=SimpleNamespace(breaker=CircuitBreaker()))
    families = {
        f.name: f
        for f in metrics.RouterCollector(orch, gateway, SimpleNamespace(dropped=2)).collect()
    }
    assert families["router_policy_fallbacks"].samples[0].value == 3
    assert families["router_egress_blocked"].samples[0].value == 5
    assert families["router_log_dropped"].samples[0].value == 2
    state = {s.labels["provider"]: s.value for s in families["router_breaker_open"].samples}
    assert state == {"junior1": 1.0, "junior2": 0.0, "senior": 0.0}


def test_metrics_endpoint_serves_prometheus_text():
    r = TestClient(app).get("/metrics")  # no lifespan: no database needed
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert "router_requests_total" in r.text


def fake_collector() -> metrics.RouterCollector:
    from collections import Counter

    audit = SimpleNamespace(results=Counter(), skipped=Counter())
    orch = SimpleNamespace(
        policy_fallbacks=0,
        audit=audit,
        j1=SimpleNamespace(breaker=CircuitBreaker()),
        j2=SimpleNamespace(breaker=CircuitBreaker()),
    )
    gateway = SimpleNamespace(blocked_count=0, senior=SimpleNamespace(breaker=CircuitBreaker()))
    return metrics.RouterCollector(orch, gateway, SimpleNamespace(dropped=0))


def known_metric_names() -> set[str]:
    """Every name the router can export: registry metrics plus the collector's families."""
    names = set()
    for fam in fake_collector().collect():
        names.add(fam.name)
        names.update(s.name for s in fam.samples)
    for m in metrics.REGISTRY.collect():
        for s in m.samples:
            names.add(s.name)
        names.add(m.name)
        names.update({f"{m.name}_bucket", f"{m.name}_sum", f"{m.name}_count", f"{m.name}_total"})
    return names


def referenced(text: str) -> set[str]:
    return set(re.findall(r"router_[a-z_]+", text))


def test_dashboard_only_uses_metrics_the_router_exports():
    dash = json.loads((ROOT / "deploy/grafana/dashboards/router.json").read_text())
    exprs = " ".join(t["expr"] for p in dash["panels"] for t in p["targets"])
    unknown = referenced(exprs) - known_metric_names()
    assert not unknown, f"dashboard uses unknown metrics: {unknown}"
    assert all(p["datasource"]["uid"] == "prometheus" for p in dash["panels"])
    ids = [p["id"] for p in dash["panels"]]
    assert len(ids) == len(set(ids))


def test_alert_rules_only_use_known_metrics_and_name_an_action():
    text = (ROOT / "deploy/prometheus/rules.yml").read_text()
    unknown = referenced(text) - known_metric_names()
    assert not unknown, f"alert rules use unknown metrics: {unknown}"
    assert text.count("- alert:") == text.count("action:") >= 5


def test_prometheus_scrapes_the_router_and_the_policy_server():
    text = (ROOT / "deploy/prometheus/prometheus.yml").read_text()
    assert "router:8000" in text and "policy-server:8082" in text and "rules.yml" in text


def test_collector_reports_audit_results_and_skips():
    c = fake_collector()
    c.orch.audit.results.update({"agree": 9, "disagree": 2})
    c.orch.audit.skipped.update({"budget": 1})
    fams = {f.name: f for f in c.collect()}
    got = {s.labels["result"]: s.value for s in fams["router_audits"].samples}
    assert got == {"agree": 9, "disagree": 2, "unreadable": 0}
    skipped = {s.labels["reason"]: s.value for s in fams["router_audits_skipped"].samples}
    assert skipped["budget"] == 1
