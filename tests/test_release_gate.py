"""Release gate (common/release_gate.py): the rule that decides whether a challenger policy may
be promoted. Shared by pipelines/revalidate.py and pipelines/shadow.py."""

import pytest

from common.metrics import wilson
from common.release_gate import ALPHA, decide, paired_counts, sign_test


def stats(correct: int, accepted: int, n: int) -> dict:
    """The three numbers the gate reads, from raw counts (as revalidate.evaluate computes them)."""
    return {
        "coverage": accepted / n,
        "accepted_accuracy": correct / accepted,
        "false_accept_upper95": wilson(accepted - correct, accepted)[1],
    }


CHAMPION = stats(correct=170, accepted=200, n=280)  # 15% false acceptance


# ---------------------------------------------------------------- sign test


def test_sign_test_known_values():
    assert sign_test(0, 0) == 1.0
    assert sign_test(5, 5) == 1.0
    assert sign_test(10, 0) == pytest.approx(2 / 1024)
    assert sign_test(3, 9) == sign_test(9, 3)
    assert 0 < sign_test(7, 13) < 1


def test_sign_test_rejects_negative_counts():
    with pytest.raises(ValueError):
        sign_test(-1, 3)


# ---------------------------------------------------------------- paired counts


def test_paired_counts_follow_the_right_call_definition():
    # right call = accept a correct junior answer, or escalate a wrong one
    champion = [True, True, False, False, True]
    challenger = [True, False, True, False, False]
    correct = [1, 0, 1, 0, 1]
    # row 1: only the challenger escalates the wrong answer   -> challenger right
    # row 2: only the challenger accepts the correct answer   -> challenger right
    # row 4: only the champion accepts the correct answer     -> champion right
    assert paired_counts(champion, challenger, correct) == (2, 1)


def test_identical_decisions_have_no_discordant_rows():
    d = [True, False, True]
    assert paired_counts(d, d, [1, 1, 0]) == (0, 0)


def test_paired_counts_need_the_same_rows():
    with pytest.raises(ValueError):
        paired_counts([True], [True, False], [1, 0])


# ---------------------------------------------------------------- verdict


def test_clear_win_passes_and_is_recorded_as_significant():
    v = decide(CHAMPION, stats(correct=176, accepted=200, n=280), better=20, worse=2)
    assert v["passed"] and all(v["criteria"].values())
    assert v["significantly_better"] and v["sign_test_p"] < ALPHA


def test_lower_accepted_accuracy_is_refused():
    v = decide(CHAMPION, stats(correct=169, accepted=200, n=280), better=5, worse=6)
    assert not v["passed"]
    assert v["criteria"] == {
        "accuracy_not_worse": False,
        "coverage_within_2pts": True,
        "false_accept_upper95_le_25pct": True,
        "not_significantly_worse": True,
    }


def test_coverage_drop_beyond_two_points_is_refused():
    # 192 / 280 accepted = 2.9 points below the champion's 200 / 280, at higher accuracy
    v = decide(CHAMPION, stats(correct=164, accepted=192, n=280), better=4, worse=4)
    assert not v["passed"] and not v["criteria"]["coverage_within_2pts"]


def test_false_acceptance_bound_above_25pct_is_refused_even_against_a_weak_champion():
    weak = stats(correct=30, accepted=50, n=60)
    v = decide(weak, stats(correct=31, accepted=50, n=60), better=1, worse=0)
    assert v["criteria"]["accuracy_not_worse"]
    assert not v["criteria"]["false_accept_upper95_le_25pct"] and not v["passed"]


def test_significantly_worse_decisions_are_refused_even_when_accuracy_looks_equal():
    v = decide(CHAMPION, stats(correct=170, accepted=200, n=280), better=2, worse=20)
    assert not v["criteria"]["not_significantly_worse"] and not v["passed"]
    assert not v["significantly_better"]


def test_a_small_loss_that_is_not_significant_does_not_trip_the_sign_test():
    v = decide(CHAMPION, stats(correct=170, accepted=200, n=280), better=9, worse=11)
    assert v["criteria"]["not_significantly_worse"] and v["passed"]
    assert v["sign_test_p"] > ALPHA and not v["significantly_better"]


# -------------------------------------------- regression: the real release-evaluation split
# Counts from results/revalidate_2026-11-11_v1.json and results/revalidate_2026-11-13_v2.json
# (1,057 questions). The discordant split is not stored, only its net difference, so every
# plausible split is tried.

RULE_V0 = stats(correct=647, accepted=767, n=1057)


@pytest.mark.parametrize("w", [0, 10, 60, 300])
def test_policy_v1_is_refused_by_a_one_question_accuracy_difference(w):
    v1 = stats(correct=646, accepted=766, n=1057)  # one fewer correct answer accepted
    v = decide(RULE_V0, v1, better=w, worse=w + 1)
    assert not v["passed"] and v["criteria"]["accuracy_not_worse"] is False
    assert v["criteria"]["not_significantly_worse"]  # the loss itself is noise


@pytest.mark.parametrize("w", [0, 10, 60, 300])
def test_policy_v2_passes_whatever_the_discordant_split(w):
    v2 = stats(correct=668, accepted=785, n=1057)  # 24 more right calls than rule-v0
    v = decide(RULE_V0, v2, better=w + 24, worse=w)
    assert v["passed"]
