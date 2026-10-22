import pytest

from common.normalizer import normalize, normalize_choice, normalize_label


@pytest.mark.parametrize(
    "text, expected",
    [
        ("B", "B"),
        ("(C)", "C"),
        ("D.", "D"),
        ("Answer: A", "A"),
        ("The answer is (B).", "B"),
        ("I think the answer is C because...", "C"),
        ("A is wrong, so the Answer: D", "D"),       # last tagged answer wins
        ("It is a tricky one. B", "B"),               # article 'a' is not option A
        ("A or B", None),                             # ambiguous -> unreadable
        ("I cannot help with that.", None),           # refusal never counts as an answer
        ("", None),
        (None, None),
    ],
)
def test_normalize_choice(text, expected):
    assert normalize_choice(text) == expected


LABELS = ["card_arrival", "card_payment_fee_charged", "lost_or_stolen_card", "card", "refund_not_showing_up"]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("card_arrival", "card_arrival"),                       # exact
        ("Card Arrival.", "card_arrival"),                      # case, spaces, punctuation
        ("Category: lost_or_stolen_card", "lost_or_stolen_card"),
        ("Answer: refund not showing up", "refund_not_showing_up"),
        ("This is about card arrival, the customer waits.", "card_arrival"),   # 'card' inside longer label ignored
        ("card", "card"),                                       # a short label is still valid on its own
        ("card_arrival or lost_or_stolen_card", None),          # two labels -> ambiguous
        ("delivery_problem", None),                             # not in the list -> unreadable
        ("I can't help with that.", None),                      # refusal
        ("", None),
        (None, None),
    ],
)
def test_normalize_label(text, expected):
    assert normalize_label(text, LABELS) == expected


def test_label_needs_a_label_list():
    assert normalize_label("card_arrival", []) is None


def test_number_is_out_of_scope():
    with pytest.raises(ValueError):
        normalize("number", "Answer: 18")
