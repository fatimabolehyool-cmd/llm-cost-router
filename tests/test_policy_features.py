"""Feature contracts policy-features-v1 and v2 (common/policy_features.py)."""

import math

import pytest

from common.policy_features import (
    FEATURES,
    FEATURES_V2,
    answer_prior,
    build,
    build_v2,
    fit_answer_prior,
    junior_pick,
)


def row(task, a1, a2, correct):
    return {"task_type": task, "j1_answer": a1, "j2_answer": a2, "junior_correct": correct}


def test_v1_vector_has_the_contract_order_and_length():
    v = build("label", 77, True, True, False, 600, 3, 0)
    assert len(v) == len(FEATURES) == 9
    assert v[:6] == [0.0, 1.0, math.log(77), 1.0, 1.0, 0.0]
    assert v[6] == pytest.approx(math.log1p(600))


def test_v2_is_v1_plus_the_prior_at_the_end():
    args = ("choice", 4, False, True, True, 120, 1, 2)
    assert build_v2(*args, 0.42) == [*build(*args), 0.42]
    assert FEATURES_V2 == [*FEATURES, "answer_prior"]


def test_prior_is_smoothed_toward_the_task_mean():
    rows = [row("label", "card_arrival", "card_arrival", 1)] * 3 + [row("label", "x", "y", 0)] * 7
    table = fit_answer_prior(rows, k=10)
    assert table["task_mean"]["label"] == pytest.approx(0.3)
    # 3 right out of 3, pulled toward 0.3 by 10 pseudo-observations: (3 + 3) / 13
    assert table["answers"]["label"]["card_arrival"] == pytest.approx(6 / 13, abs=1e-6)


def test_prior_uses_the_routers_pick():
    rows = [row("label", None, "lost_or_stolen_card", 1)] * 5
    table = fit_answer_prior(rows, k=0)
    assert table["answers"]["label"]["lost_or_stolen_card"] == 1.0
    assert junior_pick(None, "b") == "b" and junior_pick("a", "b") == "a"


def test_unknown_or_missing_answers_fall_back_to_the_task_mean():
    table = fit_answer_prior([row("choice", "A", "A", 1), row("choice", "B", "C", 0)], k=10)
    assert answer_prior(table, "choice", None, None) == table["task_mean"]["choice"]
    assert answer_prior(table, "choice", "D", None) == table["task_mean"]["choice"]
    assert answer_prior(table, "label", "anything", None) == 0.5  # task never seen


def test_rows_without_junior_answers_only_feed_the_task_mean():
    table = fit_answer_prior([{"task_type": "choice", "junior_correct": 1}], k=10)
    assert table["answers"] == {} and table["task_mean"] == {"choice": 1.0}
