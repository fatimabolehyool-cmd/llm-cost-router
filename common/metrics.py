"""Small, dependency-free statistics shared by the pipelines and the evaluation."""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion k/n (well behaved near 0 and 1)."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def percentile(xs: Sequence[float], p: float) -> float:
    """Linear-interpolated percentile, p in [0, 100]."""
    if not xs:
        return float("nan")
    s = sorted(xs)
    k = (len(s) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def bootstrap_ci(
    n: int, stat: Callable[[list[int]], float], reps: int = 2000, seed: int = 7
) -> tuple[float, float]:
    """95% percentile-bootstrap interval of stat(indices), resampling n paired items."""
    rng = random.Random(seed)
    values = sorted(stat([rng.randrange(n) for _ in range(n)]) for _ in range(reps))
    return (values[int(0.025 * reps)], values[int(0.975 * reps) - 1])
