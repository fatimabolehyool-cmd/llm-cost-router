"""Answer normalizer: turns free-text model output into a comparable answer.

This is the prototype of the router's `AnswerNormalizer` module (proposal FR-2, test U1/A4).
Supported task types (scope decision, 22 Oct 2026): "choice" (A-D) and "label" (one category
from a closed list). "number" was dropped from scope.

Rule: if an answer cannot be read with confidence, return None. The router treats None
as "disagreement", so an unreadable answer can never cause a false "agree".
"""
from __future__ import annotations

import re

SUPPORTED_TASK_TYPES = ("choice", "label")

# ---------- choice ------------------------------------------------------------------------

# "Answer: B", "answer is (C)", "The answer: D" -- the word is case-insensitive, the letter is NOT,
# so the English article "a" is never read as option A.
_ANSWER_TAG = re.compile(r"(?i:answer)\s*(?:(?i:is))?\s*[:\-]?\s*\(?([A-D])\)?(?![A-Za-z])")
_STANDALONE = re.compile(r"(?<![A-Za-z])([A-D])(?![A-Za-z])")


_LEADING_LETTER = re.compile(r"^\(?([A-D])\)?(?:[.):\-]\s*|\s+)(.*)$", re.S)


def normalize_choice(text: str | None, options: list[str] | None = None) -> str | None:
    """Return 'A'..'D' or None if the letter is missing or ambiguous.

    `options` (the four option texts, in order) lets the reader accept answers whose option
    text itself contains letters, e.g. "C. both A and B" (found in Experiment 1a, 22 Oct),
    and answers that repeat the option text without a letter.
    """
    if not text or not text.strip():
        return None
    t = text.strip()
    tagged = _ANSWER_TAG.findall(t)
    if tagged:
        return tagged[-1]
    if options and len(options) == 4:
        opts = [_canon(o) for o in options]
        m = _LEADING_LETTER.match(t)
        if m:
            rest = _canon(m.group(2))
            if rest == opts["ABCD".index(m.group(1))]:   # "C. both A and B" -> C
                return m.group(1)
        whole = _canon(t)
        if whole and opts.count(whole) == 1:              # "both A and B" -> C
            return "ABCD"[opts.index(whole)]
    # Without an "Answer:" tag, accept only if exactly one option letter appears.
    # This rejects some readable answers ("B, because A is wrong"), which is the
    # safe direction: unreadable -> escalate, never a false agreement.
    letters = set(_STANDALONE.findall(t))
    if len(letters) == 1:
        return letters.pop()
    return None  # zero or several candidate letters -> unreadable


# ---------- label -------------------------------------------------------------------------

_LABEL_TAG = re.compile(r"(?i)\b(?:answer|category|label)\s*(?:is)?\s*[:\-]\s*([^\n]+)")


def _canon(s: str) -> str:
    """'Card_Arrival.' -> 'card arrival' (lowercase, punctuation and underscores -> spaces)."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s.lower()).split())


def normalize_label(text: str | None, labels: list[str]) -> str | None:
    """Return exactly one label from `labels` (original spelling) or None if missing/ambiguous."""
    if not text or not text.strip() or not labels:
        return None
    by_canon = {_canon(l): l for l in labels}

    whole = _canon(text)
    if whole in by_canon:                      # the model replied with just the label
        return by_canon[whole]

    tagged = _LABEL_TAG.findall(text)          # "Category: card_arrival"
    if tagged:
        c = _canon(tagged[-1])
        if c in by_canon:
            return by_canon[c]

    # Otherwise: labels mentioned as whole phrases; drop any contained in a longer match
    # ("card" inside "card arrival"). Accept only if exactly one remains.
    padded = f" {whole} "
    found = [c for c in by_canon if f" {c} " in padded]
    found = [c for c in found if not any(c != o and f" {c} " in f" {o} " for o in found)]
    if len(found) == 1:
        return by_canon[found[0]]
    return None


# ---------- dispatch ----------------------------------------------------------------------

def normalize(task_type: str, text: str | None, options: list[str] | None = None) -> str | None:
    """`options`: the four option texts for choice, the category names for label."""
    if task_type == "choice":
        return normalize_choice(text, options)
    if task_type == "label":
        return normalize_label(text, options or [])
    raise ValueError(f"unsupported task_type: {task_type}")
