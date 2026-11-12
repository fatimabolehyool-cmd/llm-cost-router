"""common.metrics."""

import pytest

from common.metrics import bootstrap_ci, percentile, wilson


def test_wilson_contains_the_point_estimate_and_stays_in_range():
    lo, hi = wilson(87, 100)
    assert lo < 0.87 < hi and 0 <= lo and hi <= 1
    assert wilson(0, 10)[0] == 0.0 and wilson(10, 10)[1] == 1.0
    assert wilson(0, 0) == (0.0, 1.0)


def test_wilson_narrows_with_more_data():
    assert (wilson(870, 1000)[1] - wilson(870, 1000)[0]) < (wilson(87, 100)[1] - wilson(87, 100)[0])


def test_percentile_interpolates():
    assert percentile([1, 2, 3, 4], 50) == pytest.approx(2.5)
    assert percentile([5], 95) == 5


def test_bootstrap_ci_of_a_constant_difference_is_that_constant():
    lo, hi = bootstrap_ci(50, lambda idx: 1.0)
    assert lo == hi == 1.0


def test_bootstrap_ci_brackets_the_mean():
    data = [1] * 70 + [0] * 30
    lo, hi = bootstrap_ci(len(data), lambda idx: sum(data[i] for i in idx) / len(idx))
    assert lo < 0.7 < hi
