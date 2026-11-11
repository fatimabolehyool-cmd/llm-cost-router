"""Feature contract for the routing policy (policy-features-v1).

The router builds this vector after both juniors answer and sends it to the policy server.
The order is part of the interface contract: changing it requires a new feature version,
a retrained model and a new registered model version.

v1 (22 Oct 2026): task types are choice and label only; `task_is_number` was replaced by
`log_n_options`, because random agreement between two juniors is 1/K for K options
(the two-option weakness found in the proposal pre-test).
"""

from __future__ import annotations

import math

FEATURE_VERSION = "policy-features-v1"
FEATURES = [
    "task_is_choice",  # 0/1, from the request's task_type
    "task_is_label",  # 0/1
    "log_n_options",  # ln(number of allowed answers): 4 for choice, 77 for Banking77
    "juniors_agree",  # 0/1, normalized answers equal and both readable
    "j1_readable",  # 0/1, normalizer returned an answer
    "j2_readable",  # 0/1
    "log_prompt_tokens",  # ln(1 + prompt tokens reported by junior 1)
    "log_j1_output_tokens",  # ln(1 + output tokens of junior 1)
    "log_j2_output_tokens",  # ln(1 + output tokens of junior 2)
]


def build(
    task_type: str,
    n_options: int,
    agree: bool,
    j1_ok: bool,
    j2_ok: bool,
    prompt_tokens: int,
    j1_tokens: int,
    j2_tokens: int,
) -> list[float]:
    return [
        float(task_type == "choice"),
        float(task_type == "label"),
        math.log(max(n_options, 1)),
        float(agree),
        float(j1_ok),
        float(j2_ok),
        math.log1p(prompt_tokens),
        math.log1p(j1_tokens),
        math.log1p(j2_tokens),
    ]


# ---------------------------------------------------------------- policy-features-v2 (11 Nov 2026)
#
# v2 = v1 + `answer_prior`: how often the router's junior pick was right on the TRAINING split
# for that exact answer (e.g. the Banking77 category "card_arrival"), smoothed toward the task's
# overall accuracy. Found on the real replay pool: with v1 the classifier only re-learned the
# agreement rule; the prior lets it trust reliable categories and doubt confusable ones.
# The prior table is fitted by the pipeline, shipped inside the model package
# (policy_meta.json) and looked up by the router, so training and serving use the same numbers.

FEATURE_VERSION_V2 = "policy-features-v2"
FEATURES_V2 = [*FEATURES, "answer_prior"]
PRIOR_SMOOTHING = 10  # pseudo-observations at the task's overall accuracy


def junior_pick(a1: str | None, a2: str | None) -> str | None:
    """The answer the router would return from the juniors (junior 1's if readable)."""
    return a1 if a1 is not None else a2


def fit_answer_prior(rows: list[dict], k: int = PRIOR_SMOOTHING) -> dict:
    """Fit the prior table from training rows (replay schema: task_type, j1_answer, j2_answer,
    junior_correct). Rows without junior answers (the synthetic fixture) only feed the task mean."""
    totals: dict[str, list[int]] = {}
    per_answer: dict[str, dict[str, list[int]]] = {}
    for r in rows:
        t = r["task_type"]
        totals.setdefault(t, [0, 0])
        totals[t][0] += int(r["junior_correct"])
        totals[t][1] += 1
        pick = junior_pick(r.get("j1_answer"), r.get("j2_answer"))
        if pick is not None:
            c = per_answer.setdefault(t, {}).setdefault(pick, [0, 0])
            c[0] += int(r["junior_correct"])
            c[1] += 1
    task_mean = {t: c / n for t, (c, n) in totals.items() if n}
    answers = {
        t: {a: round((c + k * task_mean[t]) / (n + k), 6) for a, (c, n) in counts.items()}
        for t, counts in per_answer.items()
    }
    return {"smoothing_k": k, "task_mean": task_mean, "answers": answers}


def answer_prior(table: dict, task_type: str, a1: str | None, a2: str | None) -> float:
    """Look up the prior for the router's pick; unknown answers get the task's mean."""
    mean = table["task_mean"].get(task_type, 0.5)
    pick = junior_pick(a1, a2)
    if pick is None:
        return mean
    return table["answers"].get(task_type, {}).get(pick, mean)


def build_v2(
    task_type: str,
    n_options: int,
    agree: bool,
    j1_ok: bool,
    j2_ok: bool,
    prompt_tokens: int,
    j1_tokens: int,
    j2_tokens: int,
    prior: float,
) -> list[float]:
    return [
        *build(task_type, n_options, agree, j1_ok, j2_ok, prompt_tokens, j1_tokens, j2_tokens),
        float(prior),
    ]
