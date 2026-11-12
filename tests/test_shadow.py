"""Offline shadow comparison (pipelines/shadow.py): features, decisions, counts and the report.

Pure functions only: MLflow and scikit-learn are not needed (fake predict functions)."""

import json
import math

import pytest

from common.policy_features import FEATURES, FEATURES_V2, fit_answer_prior
from pipelines import shadow
from router.policy import PolicyMeta

PRIOR = fit_answer_prior(
    [
        {"task_type": "choice", "j1_answer": "A", "j2_answer": "A", "junior_correct": 1},
        {"task_type": "choice", "j1_answer": "B", "j2_answer": "C", "junior_correct": 0},
        {
            "task_type": "label",
            "j1_answer": "card_lost",
            "j2_answer": "card_lost",
            "junior_correct": 1,
        },
    ]
)


def row(a1="A", a2="A", correct=1, task="choice", n=4, pt=120, t1=2, t2=3):
    return {
        "task_type": task,
        "n_options": n,
        "j1_answer": a1,
        "j2_answer": a2,
        "prompt_tokens": pt,
        "j1_tokens": t1,
        "j2_tokens": t2,
        "junior_correct": correct,
        "group": f"{task}:{a1}-{a2}-{pt}",
    }


ROWS = [
    row(),
    row("A", "C", 0),
    row("A", None, 1, t2=40),
    row(None, "B", 0, t1=50),
    row(None, None, 0),
    row("card_lost", "card_lost", 1, task="label", n=77, pt=300),
    row("card_lost", "refund", 0, task="label", n=77, pt=310),
]


def meta(version="policy-features-v2", threshold=0.6, name="run-chal0001"):
    features = FEATURES_V2 if version.endswith("v2") else FEATURES
    m = {"model_version": name, "feature_version": version, "features": features}
    m["thresholds"] = {"balanced": threshold}
    m["answer_prior"] = PRIOR if version.endswith("v2") else None
    return m


@pytest.mark.parametrize("version", ["policy-features-v1", "policy-features-v2"])
def test_features_match_what_the_router_computes_at_serving_time(tmp_path, version):
    """No train/serve skew: the shadow scores exactly the vector the router would send."""
    m = meta(version)
    path = tmp_path / "policy_meta.json"
    path.write_text(json.dumps(m))
    router_meta = PolicyMeta.load(path)
    for r in ROWS:
        served = router_meta.vector(
            r["task_type"], r["n_options"], r["j1_answer"], r["j2_answer"],
            r["prompt_tokens"], r["j1_tokens"], r["j2_tokens"],
        )  # fmt: skip
        assert shadow.vector(m, r) == pytest.approx(served)


def test_unknown_feature_version_is_an_error():
    with pytest.raises(ValueError):
        shadow.vector(meta("policy-features-v9"), ROWS[0])


def test_rule_v0_accepts_only_readable_agreement():
    assert shadow.rule_v0().decide(ROWS) == [True, False, False, False, False, True, False]


def test_model_policy_applies_its_threshold_and_never_scores_unreadable_rows():
    seen = []

    def predict(xs):
        seen.extend(xs)
        return [0.7, 0.5, 0.65, 0.9, 0.61, 0.59]  # one per row with a readable answer

    decisions = shadow.model_policy(meta(threshold=0.6), predict).decide(ROWS)
    assert len(seen) == 6  # row 4 (both unreadable) is escalated without asking the model
    assert decisions == [True, False, True, True, False, True, False]


def test_sign_test_known_values():
    assert shadow.sign_test(0, 0) == 1.0
    assert shadow.sign_test(5, 5) == 1.0
    assert shadow.sign_test(10, 0) == pytest.approx(2 / 1024)
    assert shadow.sign_test(3, 9) == shadow.sign_test(9, 3)
    assert 0 < shadow.sign_test(7, 13) < 1


def fixed(name, decisions):
    return shadow.Policy(name, lambda rows: list(decisions))


def test_compare_counts_every_changed_decision():
    # rows:        0  1  2  3  4  5  6     correct = 1 0 1 0 0 1 0
    champion = fixed("run-champ", [1, 1, 0, 0, 0, 1, 0])
    challenger = fixed("run-chal", [1, 0, 1, 0, 0, 1, 1])
    r = shadow.compare(ROWS, champion, challenger, senior_cost=0.01)
    d = r["decisions"]
    assert (d["same"], d["now_escalated"], d["now_accepted"]) == (4, 1, 2)
    assert d["now_escalated_wrong_answers"] == 1  # row 1: a wrong answer no longer accepted
    assert d["now_accepted_wrong_answers"] == 1  # row 6: a wrong answer newly accepted
    assert (d["challenger_right_only"], d["champion_right_only"]) == (2, 1)
    assert d["sign_test_p"] == pytest.approx(1.0)
    assert r["champion"]["coverage"] == pytest.approx(3 / 7)
    assert r["challenger"]["accepted_accuracy"] == pytest.approx(3 / 4)
    assert r["champion"]["senior_usd_per_1000"] == pytest.approx(round(1000 * 4 / 7 * 0.01, 2))
    # champion accepts rows 0, 1, 5 (2 of 3 correct); challenger rows 0, 2, 5, 6 (3 of 4): more
    # accurate, but one error in four answers gives a false-acceptance upper bound far above 25%
    assert r["gate_vs_champion"] == {
        "accuracy_not_worse": True,
        "coverage_within_2pts": True,
        "false_accept_upper95_le_25pct": False,
    }
    assert r["verdict"] == "keep the champion"


def test_identical_policies_change_nothing():
    same = [1, 0, 0, 0, 0, 1, 0]
    r = shadow.compare(ROWS, fixed("a", same), fixed("b", same), senior_cost=0.0027)
    assert r["decisions"]["same"] == len(ROWS) and r["decisions"]["sign_test_p"] == 1.0
    assert r["gate_vs_champion"]["accuracy_not_worse"]
    assert r["gate_vs_champion"]["coverage_within_2pts"]


def test_report_is_readable_and_tables_stay_narrow():
    r = shadow.compare(ROWS, shadow.rule_v0(), fixed("run-chal", [1, 0, 1, 0, 0, 1, 0]), 0.0027)
    text = shadow.render(r)
    assert text.startswith("# Shadow comparison: run-chal (challenger) vs rule-v0 (champion)")
    assert "Verdict:" in text and "sign test" in text and "Nothing was served" in text
    for line in text.splitlines():
        if line.startswith("|"):
            assert line.count("|") <= 4  # at most 3 columns (architecture feedback A6)
    assert not any(isinstance(v, float) and math.isnan(v) for v in r["challenger"].values())
