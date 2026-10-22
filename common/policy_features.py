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
    "task_is_choice",       # 0/1, from the request's task_type
    "task_is_label",        # 0/1
    "log_n_options",        # ln(number of allowed answers): 4 for choice, 77 for Banking77
    "juniors_agree",        # 0/1, normalized answers equal and both readable
    "j1_readable",          # 0/1, normalizer returned an answer
    "j2_readable",          # 0/1
    "log_prompt_tokens",    # ln(1 + prompt tokens reported by junior 1)
    "log_j1_output_tokens", # ln(1 + output tokens of junior 1)
    "log_j2_output_tokens", # ln(1 + output tokens of junior 2)
]


def build(task_type: str, n_options: int, agree: bool, j1_ok: bool, j2_ok: bool,
          prompt_tokens: int, j1_tokens: int, j2_tokens: int) -> list[float]:
    return [
        float(task_type == "choice"), float(task_type == "label"), math.log(max(n_options, 1)),
        float(agree), float(j1_ok), float(j2_ok),
        math.log1p(prompt_tokens), math.log1p(j1_tokens), math.log1p(j2_tokens),
    ]
