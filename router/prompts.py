"""Junior prompt construction, shared by the router and the offline pipelines.

The router (serving) and `pipelines.build_replay` (training data) must build byte-identical
prompts; otherwise the policy is trained on behaviour the router never produces (train/serve
skew). Both therefore call `build_prompt` with the versioned prompt file (ADR-17).
"""

from __future__ import annotations

import json
from pathlib import Path

CHOICE_LETTERS = ("A", "B", "C", "D")


def load_prompts(path: str | Path) -> dict:
    """Read a versioned prompt file such as prompts/junior_prompts_v2.json."""
    prompts = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("version", "choice", "label"):
        if key not in prompts:
            raise ValueError(f"prompt file {path} has no '{key}' entry")
    return prompts


def build_prompt(
    prompts: dict, task_type: str, question: str, options: list[str]
) -> tuple[str, str, int]:
    """Return (system message, user message, max output tokens) for one junior call.

    `options` are the four option texts for `choice` and the category names for `label`.
    """
    if task_type not in ("choice", "label"):
        raise ValueError(f"unsupported task_type: {task_type}")
    spec = prompts[task_type]
    if task_type == "choice":
        opts = "\n".join(
            f"{letter}. {o}" for letter, o in zip(CHOICE_LETTERS, options, strict=True)
        )
    else:
        opts = "\n".join(options)
    return (
        spec["system"],
        spec["user_template"].format(question=question, options=opts),
        spec["num_predict"],
    )
