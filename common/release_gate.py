"""Release gate for the routing policy: one definition for every caller.

Used by the re-validation pipeline (pipelines/revalidate.py, challenger vs rule-v0) and by the
shadow report (pipelines/shadow.py, challenger vs the serving champion), so the two can never
disagree about what "eligible for promotion" means. Dependency-free, so it is unit-tested in CI.

A routing decision is RIGHT when it accepts a correct junior answer or escalates a wrong one.
Two policies decided on the same rows are compared on the rows where exactly one of them is
right (the discordant rows) with an exact two-sided sign test.

Criteria (all must hold):
  accuracy_not_worse            challenger accepted-answer accuracy >= champion's (point estimate;
                                ties and noise favour the champion, the safe direction)
  coverage_within_2pts          challenger accepts at most 2 points fewer requests
  false_accept_upper95_le_25pct Wilson upper 95% bound of false acceptance <= 25% (NFR-10)
  not_significantly_worse       the champion is not right significantly more often on the
                                discordant rows (sign test, alpha 0.05)
Recorded, not required: the sign-test p-value and whether the challenger is significantly
better. Promotion stays a human decision (pipelines.registry promote).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

MAX_COVERAGE_DROP = 0.02
MAX_FALSE_ACCEPT_UPPER = 0.25
ALPHA = 0.05


def sign_test(k: int, m: int) -> float:
    """Two-sided exact binomial test that k and m successes out of k + m are equally likely."""
    if k < 0 or m < 0:
        raise ValueError("counts must be non-negative")
    n = k + m
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(k, m) + 1)) / 2**n
    return min(1.0, 2 * tail)


def paired_counts(
    champion_accept: Sequence[bool],
    challenger_accept: Sequence[bool],
    junior_correct: Sequence[int],
) -> tuple[int, int]:
    """(challenger_right_only, champion_right_only) over the same rows."""
    if not len(champion_accept) == len(challenger_accept) == len(junior_correct):
        raise ValueError("decisions and labels must cover the same rows")
    better = worse = 0
    for a, b, y in zip(champion_accept, challenger_accept, junior_correct, strict=True):
        right_a = bool(a) == bool(y)  # accept a correct answer, or escalate a wrong one
        right_b = bool(b) == bool(y)
        if right_b and not right_a:
            better += 1
        elif right_a and not right_b:
            worse += 1
    return better, worse


def decide(
    champion: Mapping[str, float],
    challenger: Mapping[str, float],
    better: int,
    worse: int,
    alpha: float = ALPHA,
) -> dict:
    """Gate verdict. champion/challenger need coverage, accepted_accuracy, false_accept_upper95."""
    p = sign_test(better, worse)
    criteria = {
        "accuracy_not_worse": challenger["accepted_accuracy"] >= champion["accepted_accuracy"],
        "coverage_within_2pts": challenger["coverage"] >= champion["coverage"] - MAX_COVERAGE_DROP,
        "false_accept_upper95_le_25pct": challenger["false_accept_upper95"]
        <= MAX_FALSE_ACCEPT_UPPER,
        "not_significantly_worse": not (worse > better and p < alpha),
    }
    return {
        "criteria": criteria,
        "passed": all(criteria.values()),
        "challenger_right_only": better,
        "champion_right_only": worse,
        "sign_test_p": p,
        "significantly_better": better > worse and p < alpha,
    }
