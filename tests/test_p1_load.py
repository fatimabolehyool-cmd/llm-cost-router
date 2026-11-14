"""P1 load test: how a scenario is scored (pure functions; the run itself needs the mock stack)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "smoke"))
from p1_load import expected, passed, summarize  # noqa: E402


def res(code=200, ms=520.0, status="answered", escalated=False, latency=510):
    out = {"status": status, "escalated": escalated, "latency_ms": latency} if code == 200 else None
    return {"code": code, "ms": ms, "out": out}


def test_each_scenario_expects_its_own_route():
    assert expected("agree", {"status": "answered", "escalated": False})
    assert not expected("agree", {"status": "answered", "escalated": True})
    assert expected("disagree", {"status": "answered", "escalated": True})
    assert not expected("disagree", {"status": "abstained", "escalated": False})


def test_overhead_is_router_latency_minus_model_time_and_failures_lower_success():
    rows = [res(latency=510), res(latency=530), res(code=503), res(escalated=True)]
    s = summarize("agree", 4, rows, wall_s=2.0, model_ms=500)
    assert s["success"] == 0.5 and s["throughput_rps"] == 2.0
    assert s["overhead_p50_ms"] == pytest.approx(20) and s["overhead_max_ms"] == pytest.approx(30)
    assert s["codes"] == {200: 3, 503: 1}


def test_the_run_passes_only_if_every_scenario_meets_both_targets():
    good = {"overhead_p95_ms": 40.0, "success": 1.0}
    assert passed([good, good])
    assert not passed([good, {"overhead_p95_ms": 250.0, "success": 1.0}])
    assert not passed([good, {"overhead_p95_ms": 40.0, "success": 0.97}])
