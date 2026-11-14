"""FR-17 per-category report: what counts as a category, when one is flagged, and how the
always-escalate list changes routing."""

import pytest

from pipelines.category_report import category, category_stats, flagged, simulate, subject_stats


def row(qid, answer, gold, task="label", agreed=True, subject="banking77"):
    return {
        "question_id": qid,
        "task_type": task,
        "agreed": agreed,
        "j1_answer": answer,
        "gold": gold,
        "subject": subject,
    }


def test_only_agreed_help_desk_messages_have_a_category():
    assert category(row("1", "card_arrival", "card_arrival")) == "card_arrival"
    assert category(row("2", "card_arrival", "card_arrival", agreed=False)) is None
    assert category(row("3", "B", "B", task="choice")) is None  # no category at request time


def test_false_agreement_is_counted_per_agreed_category():
    rows = [
        row("1", "a", "a"),
        row("2", "a", "b"),
        row("3", "b", "b"),
        row("4", "x", "y", agreed=False),
    ]
    stats = category_stats(rows)
    assert set(stats) == {"a", "b"}
    assert (stats["a"]["agreed"], stats["a"]["wrong"], stats["a"]["fa"]) == (2, 1, 0.5)
    assert stats["a"]["fa_upper95"] > stats["a"]["fa"]


def test_a_category_is_flagged_only_with_enough_answers_and_a_high_observed_rate():
    stats = {
        "fails": {"agreed": 10, "wrong": 4, "fa": 0.4, "fa_upper95": 0.69},
        "few": {"agreed": 3, "wrong": 3, "fa": 1.0, "fa_upper95": 1.0},  # too little evidence
        "clean": {"agreed": 9, "wrong": 0, "fa": 0.0, "fa_upper95": 0.30},  # high bound, no errors
    }
    assert flagged(stats) == ["fails"]
    assert flagged(stats, min_agreed=3) == ["fails", "few"]


def test_exam_subjects_are_reported_but_never_flagged():
    rows = [
        row("1", "A", "B", task="choice", subject="law"),
        row("2", "A", "A", task="choice", subject="law"),
    ]
    assert subject_stats(rows) == {"law": {"agreed": 2, "wrong": 1, "fa": 0.5}}
    assert category_stats(rows) == {}


def test_the_list_sends_its_categories_to_opus_and_keeps_the_rest_local():
    rows = [row("1", "a", "b"), row("2", "c", "c"), row("3", "d", "d", agreed=False)]
    opus = {
        "1": {"ok": True, "answer": "b", "cost_usd": 0.003},
        "3": {"ok": True, "answer": "d", "cost_usd": 0.002},
    }
    base = simulate(rows, opus, set())
    assert base["accuracy"] == pytest.approx(2 / 3) and base["escalation"] == pytest.approx(1 / 3)
    listed = simulate(rows, opus, {"a"})
    assert listed["accuracy"] == 1.0 and listed["escalation"] == pytest.approx(2 / 3)
    assert listed["cost_per_1000"] == pytest.approx(1000 * 0.005 / 3)


def test_a_failed_opus_call_counts_as_wrong():
    rows = [row("1", "a", "b", agreed=False)]
    assert (
        simulate(rows, {"1": {"ok": False, "answer": None, "cost_usd": 0.0}}, set())["accuracy"]
        == 0.0
    )
