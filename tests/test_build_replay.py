"""Unit tests for the replay-pool builder's pure logic (no Ollama, no downloads)."""

import json

from common.splits import SPLITS, split_of
from pipelines.build_replay import (
    group_key,
    junior_pick,
    options_for,
    read_jsonl,
    to_replay_row,
    write_with_checksum,
)

Q_CHOICE = {
    "question_id": "mmlu-test-1",
    "group": "choice:what is 2 2",
    "task_type": "choice",
    "subject": "math",
    "options": ["3", "4", "5", "6"],
    "n_options": 4,
    "gold": "B",
}


def answers(a1, a2, ok1=True, ok2=True, ms1=900.0, ms2=1100.0):
    return {
        "question_id": "mmlu-test-1",
        "j1_ok": ok1,
        "j2_ok": ok2,
        "j1_ms": ms1,
        "j2_ms": ms2,
        "j1_answer": a1,
        "j2_answer": a2,
        "j1_tokens": 1,
        "j2_tokens": 2,
        "prompt_tokens": 40,
    }


def test_group_key_ignores_case_spacing_and_punctuation():
    assert group_key("label", "Where is my CARD?!") == group_key("label", "where  is my card")


def test_group_key_keeps_task_types_apart():
    assert group_key("label", "same text") != group_key("choice", "same text")


def test_near_duplicates_always_land_in_the_same_split():
    a, b = (
        group_key("label", "My card hasn't arrived."),
        group_key("label", "my card hasnt arrived"),
    )
    assert split_of(a) == split_of(b)


def test_junior_pick_matches_the_orchestrator():
    assert junior_pick("A", "B") == "A"
    assert junior_pick(None, "B") == "B"
    assert junior_pick(None, None) is None


def test_options_for_uses_shared_categories_for_label():
    labels = ["card_arrival", "lost_or_stolen_card"]
    assert options_for({"task_type": "label", "options": None}, labels) == labels
    assert options_for(Q_CHOICE, labels) == Q_CHOICE["options"]


def test_agreed_and_correct():
    row = to_replay_row(Q_CHOICE, answers("B", "B"), timeout_ms=8000)
    assert row["agreed"] is True and row["junior_correct"] == 1
    assert row["label_source"] == "gold" and row["junior_stage_ms"] == 1100.0


def test_agreed_but_wrong_is_a_false_agreement():
    row = to_replay_row(Q_CHOICE, answers("C", "C"), timeout_ms=8000)
    assert row["agreed"] is True and row["junior_correct"] == 0


def test_unreadable_answers_never_count_as_agreement():
    row = to_replay_row(Q_CHOICE, answers(None, None), timeout_ms=8000)
    assert row["agreed"] is False and row["j1_ok"] is False and row["junior_correct"] == 0


def test_answer_slower_than_router_timeout_counts_as_unavailable():
    row = to_replay_row(Q_CHOICE, answers("B", "B", ms2=9500.0), timeout_ms=8000)
    assert row["agreed"] is False and row["j2_ok"] is False
    assert row["junior_correct"] == 1  # junior 1 is still the router's pick


def test_failed_call_counts_as_unavailable():
    row = to_replay_row(Q_CHOICE, answers("A", "B", ok1=False), timeout_ms=8000)
    assert row["j1_ok"] is False and row["j1_answer"] is None and row["junior_correct"] == 1


def test_write_with_checksum_round_trip(tmp_path):
    path = tmp_path / "rows.jsonl"
    digest = write_with_checksum(path, [{"a": 1}, {"b": "é"}])
    assert read_jsonl(path) == [{"a": 1}, {"b": "é"}]
    assert path.with_suffix(".sha256").read_text().split()[0] == digest


def test_replay_rows_keep_the_schema_revalidate_reads():
    row = to_replay_row(Q_CHOICE, answers("B", "A"), timeout_ms=8000)
    needed = {
        "group",
        "task_type",
        "n_options",
        "agreed",
        "j1_ok",
        "j2_ok",
        "prompt_tokens",
        "j1_tokens",
        "j2_tokens",
        "junior_correct",
        "label_source",
    }
    assert needed <= row.keys()
    json.dumps(row)  # serializable


def test_split_shares_sum_to_one_and_every_key_gets_a_known_split():
    assert abs(sum(share for _, share in SPLITS) - 1.0) < 1e-9
    names = {name for name, _ in SPLITS}
    assert all(split_of(f"label:q{i}") in names for i in range(500))


def test_split_proportions_are_close_to_the_contract():
    counts = {name: 0 for name, _ in SPLITS}
    for i in range(20000):
        counts[split_of(f"choice:question {i}")] += 1
    for name, share in SPLITS:
        assert abs(counts[name] / 20000 - share) < 0.015
