from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import stats as sps

from abmfact import stats as st


def test_mean_interval_matches_scipy():
    x = np.array([1.0, 2.0, 3.0, 4.0, 10.0])
    iv = st.mean_interval(x, 0.05)
    lo, hi = sps.t.interval(0.90, df=4, loc=x.mean(), scale=sps.sem(x))
    assert iv.low == pytest.approx(lo) and iv.high == pytest.approx(hi)
    assert iv.half_width == pytest.approx((hi - lo) / 2)
    one = st.mean_interval(np.array([3.0]), 0.05)
    assert (one.low, one.high, one.n) == (3.0, 3.0, 1)
    with pytest.raises(ValueError):
        st.mean_interval(np.array([]), 0.05)


def test_diff_interval_welch():
    rng = np.random.default_rng(0)
    a, b = rng.normal(0, 1, 50), rng.normal(1, 2, 40)
    iv = st.diff_interval(a, b, 0.05)
    se = math.sqrt(a.var(ddof=1) / 50 + b.var(ddof=1) / 40)
    assert iv.estimate == pytest.approx(b.mean() - a.mean())
    assert iv.sd == pytest.approx(se)
    assert iv.low < iv.estimate < iv.high
    flat = st.diff_interval(np.ones(5), np.ones(5) * 2, 0.05)
    assert (flat.low, flat.high) == (1.0, 1.0)
    single = st.diff_interval(np.array([1.0]), np.array([1.0, 2.0, 3.0]), 0.05)
    assert single.low < single.estimate < single.high


def test_slope_interval():
    x = np.repeat([0.0, 1.0, 2.0], 20)
    y = 3 * x + np.random.default_rng(1).normal(0, 0.1, x.size)
    iv = st.slope_interval(x, y, 0.05)
    assert iv.low < 3 < iv.high
    exact = st.slope_interval(np.array([0.0, 1, 2]), np.array([0.0, 1, 2]), 0.05)
    assert exact.low == exact.high == pytest.approx(1.0)
    with pytest.raises(ValueError):
        st.slope_interval(np.ones(5), np.arange(5.0), 0.05)


@pytest.mark.parametrize(
    ("test", "value", "lo", "hi", "expected"),
    [
        ("greater_than", 1.0, 2, 3, "holds"),
        ("greater_than", 1.0, 0, 0.5, "fails"),
        ("greater_than", 1.0, 0, 2, "uncertain"),
        ("less_than", 1.0, 0, 0.5, "holds"),
        ("less_than", 1.0, 2, 3, "fails"),
        ("less_than", 1.0, 0, 2, "uncertain"),
        ("between", (0.0, 1.0), 0.2, 0.8, "holds"),
        ("between", (0.0, 1.0), 2, 3, "fails"),
        ("between", (0.0, 1.0), -1, -0.5, "fails"),
        ("between", (0.0, 1.0), 0.5, 1.5, "uncertain"),
    ],
)
def test_side_status(test, value, lo, hi, expected):
    assert st.side_status(st.Interval((lo + hi) / 2, lo, hi, 10), test, value) == expected


def test_side_status_rejects_trend():
    with pytest.raises(ValueError):
        st.side_status(st.Interval(0, 0, 0, 1), "monotone_increasing", None)


def test_trend_status():
    up = st.Interval(1, 0.5, 1.5, 10)
    down = st.Interval(-1, -1.5, -0.5, 10)
    flat = st.Interval(0, -0.01, 0.01, 10)
    wide = st.Interval(0, -2, 2, 10)
    assert st.trend_status(up, True, 0.0) == "holds"
    assert st.trend_status(down, True, 0.0) == "fails"
    assert st.trend_status(down, False, 0.0) == "holds"
    assert st.trend_status(flat, True, 0.1) == "fails"
    assert st.trend_status(flat, True, 0.0) == "uncertain"
    assert st.trend_status(wide, True, 0.1) == "uncertain"


def test_equivalence_three_outcomes():
    assert st.equivalence(st.Interval(0, -0.5, 0.5, 1), 1.0) == "equivalent"
    assert st.equivalence(st.Interval(3, 2, 4, 1), 1.0) == "shifted"
    assert st.equivalence(st.Interval(-3, -4, -2, 1), 1.0) == "shifted"
    assert st.equivalence(st.Interval(1, 0.5, 1.5, 1), 1.0) == "undetermined"


def test_spread():
    rng = np.random.default_rng(2)
    a, b = rng.normal(0, 1, 200), rng.normal(0, 4, 200)
    sp = st.spread(a, b)
    assert sp.p < 1e-6 and sp.sd_ratio > 2 and sp.iqr_ratio > 2
    assert sp.shifted(0.05)
    assert st.spread(b, a).shifted(0.05)
    same = st.spread(a, rng.normal(0, 1, 200))
    assert not same.shifted(0.05)
    assert st.spread(np.ones(5), np.ones(5)) == st.Spread(1.0, 1.0, 1.0)
    tiny = st.spread(np.array([1.0, 2.0]), np.array([1.0, 3.0]))
    assert tiny.p == 1.0
    assert st.spread(np.zeros(5), np.arange(5.0)).sd_ratio == float("inf")


def test_seeds_needed_and_tolerance():
    assert st.seeds_needed(100, 0.0, 1.0) == 100
    assert st.seeds_needed(100, 1.0, 0.0) == st.MAX_SEEDS
    assert st.seeds_needed(100, 2.0, 1.0) == 400
    assert st.seeds_needed(100, 0.1, 1.0) == 101
    assert st.default_tolerance(np.array([10.0, 10.0])) == pytest.approx(1.0)
    assert st.default_tolerance(np.array([-1.0, 1.0])) == pytest.approx(0.1 * math.sqrt(2))
    assert st.default_tolerance(np.array([0.0, 0.0])) == 1e-9
    assert st._tcrit(0.05, float("inf")) == pytest.approx(sps.norm.ppf(0.95))
