"""Small, explicit statistics: intervals, three-outcome equivalence, slopes and spread."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import stats as sps

MAX_SEEDS = 10_000
SPREAD_RATIO = 2.0


@dataclass(frozen=True)
class Interval:
    """An estimate with its interval, sample size and spread.

    ``sd`` is the sample standard deviation for a mean interval, and the standard error for difference and
    slope intervals.
    """

    estimate: float
    low: float
    high: float
    n: int
    sd: float = 0.0

    @property
    def half_width(self) -> float:
        """Half the width of the interval."""
        return (self.high - self.low) / 2.0

    def to_dict(self) -> dict[str, float | int]:
        """The interval as a plain dict."""
        return {"estimate": self.estimate, "low": self.low, "high": self.high, "n": self.n, "sd": self.sd}


def _tcrit(alpha: float, df: float) -> float:
    if not math.isfinite(df) or df <= 0:
        return float(sps.norm.ppf(1 - alpha))
    return float(sps.t.ppf(1 - alpha, df))


def mean_interval(x: np.ndarray, alpha: float) -> Interval:
    """Two-sided ``1 - 2*alpha`` t-interval for the mean (so each side is a one-sided ``alpha`` test)."""
    x = np.asarray(x, dtype=float)
    n = int(x.size)
    if n == 0:
        raise ValueError("no values")
    m = float(x.mean())
    if n == 1:
        return Interval(m, m, m, 1, 0.0)
    sd = float(x.std(ddof=1))
    hw = _tcrit(alpha, n - 1) * sd / math.sqrt(n)
    return Interval(m, m - hw, m + hw, n, sd)


def diff_interval(a: np.ndarray, b: np.ndarray, alpha: float) -> Interval:
    """Welch interval for ``mean(b) - mean(a)``."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    na, nb = a.size, b.size
    d = float(b.mean() - a.mean())
    va = float(a.var(ddof=1)) if na > 1 else 0.0
    vb = float(b.var(ddof=1)) if nb > 1 else 0.0
    se2 = va / max(na, 1) + vb / max(nb, 1)
    if se2 == 0.0:
        return Interval(d, d, d, int(na + nb), 0.0)
    num = se2**2
    den = 0.0
    if na > 1:
        den += (va / na) ** 2 / (na - 1)
    if nb > 1:
        den += (vb / nb) ** 2 / (nb - 1)
    df = num / den if den > 0 else float("inf")
    hw = _tcrit(alpha, df) * math.sqrt(se2)
    return Interval(d, d - hw, d + hw, int(na + nb), math.sqrt(se2))


def slope_interval(x: np.ndarray, y: np.ndarray, alpha: float) -> Interval:
    """OLS slope of ``y`` on ``x`` with a t-interval."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n = x.size
    if n < 3 or np.ptp(x) == 0:
        raise ValueError("slope needs at least three runs over at least two distinct x values")
    xm, ym = x.mean(), y.mean()
    sxx = float(((x - xm) ** 2).sum())
    b = float(((x - xm) * (y - ym)).sum() / sxx)
    resid = y - (ym + b * (x - xm))
    s2 = float((resid**2).sum() / (n - 2))
    se = math.sqrt(s2 / sxx) if s2 > 0 else 0.0
    hw = _tcrit(alpha, n - 2) * se
    return Interval(b, b - hw, b + hw, int(n), se)


def side_status(iv: Interval, test: str, value: float | tuple[float, float] | None) -> str:
    """``holds``, ``fails`` or ``uncertain`` for a threshold test on an interval."""
    if test == "greater_than":
        assert isinstance(value, float)
        if iv.low > value:
            return "holds"
        return "fails" if iv.high < value else "uncertain"
    if test == "less_than":
        assert isinstance(value, float)
        if iv.high < value:
            return "holds"
        return "fails" if iv.low > value else "uncertain"
    if test == "between":
        assert isinstance(value, tuple)
        lo, hi = value
        if iv.low > lo and iv.high < hi:
            return "holds"
        return "fails" if iv.high < lo or iv.low > hi else "uncertain"
    raise ValueError(f"not a threshold test: {test}")


def trend_status(iv: Interval, increasing: bool, negligible: float) -> str:
    """Status of a monotone claim from a slope interval.

    ``fails`` when the slope is confidently in the wrong direction, or confidently smaller in size than
    ``negligible`` (the trend has vanished).
    """
    lo, hi = (iv.low, iv.high) if increasing else (-iv.high, -iv.low)
    if lo > 0:
        return "holds"
    if hi < 0:
        return "fails"
    if negligible > 0 and -negligible < lo and hi < negligible:
        return "fails"
    return "uncertain"


def equivalence(iv: Interval, tol: float) -> str:
    """Three outcomes for a difference interval against a margin ``tol``."""
    if -tol < iv.low and iv.high < tol:
        return "equivalent"
    if iv.low > tol or iv.high < -tol:
        return "shifted"
    return "undetermined"


def _iqr(x: np.ndarray) -> float:
    q75, q25 = np.percentile(x, [75, 25])
    return float(q75 - q25)


def _ratio(num: float, den: float) -> float:
    if num == 0.0 and den == 0.0:
        return 1.0
    return num / den if den > 0 else float("inf")


@dataclass(frozen=True)
class Spread:
    """Brown-Forsythe p-value with two scale ratios, ``b / a``: standard deviations and IQRs."""

    p: float
    sd_ratio: float
    iqr_ratio: float

    def shifted(self, alpha: float) -> bool:
        """Flag only a clear change: p below ``alpha / 10`` and both ratios beyond 2 the same way."""
        up = self.sd_ratio > SPREAD_RATIO and self.iqr_ratio > SPREAD_RATIO
        down = self.sd_ratio < 1.0 / SPREAD_RATIO and self.iqr_ratio < 1.0 / SPREAD_RATIO
        return self.p < alpha / 10 and (up or down)


def spread(a: np.ndarray, b: np.ndarray) -> Spread:
    """Compare the spread of ``b`` with that of ``a``.

    The p-value is 1 when either sample has fewer than three values or neither varies. Both ratios are ``b / a``.
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    sa = float(a.std(ddof=1)) if a.size > 1 else 0.0
    sb = float(b.std(ddof=1)) if b.size > 1 else 0.0
    sd_ratio = _ratio(sb, sa)
    iqr_ratio = _ratio(_iqr(b), _iqr(a)) if a.size > 1 and b.size > 1 else 1.0
    if (sa == 0.0 and sb == 0.0) or a.size < 3 or b.size < 3:
        return Spread(1.0, sd_ratio, iqr_ratio)
    p = float(sps.levene(a, b, center="median").pvalue)
    return Spread(p if math.isfinite(p) else 1.0, sd_ratio, iqr_ratio)


def seeds_needed(n: int, half_width: float, gap: float) -> int:
    """Seeds per condition needed for an interval of this width to clear a gap of this size."""
    if half_width <= 0:
        return n
    if gap <= 0:
        return MAX_SEEDS
    return int(min(MAX_SEEDS, max(n + 1, math.ceil(n * (half_width / gap) ** 2))))


#: Below this absolute baseline mean (in the metric's own units) a relative tolerance is a tiny number.
NEAR_ZERO = 0.01


def tolerance_warning(baseline: np.ndarray, tol: float) -> str | None:
    """Why a default relative tolerance may mislead for this baseline, or ``None``.

    Two triggers: the tolerance is smaller than the standard error of the baseline mean, so the seeds
    cannot resolve it; or the baseline mean is below :data:`NEAR_ZERO` in absolute value, so 10% of it is a
    tiny absolute margin. The second is in the metric's units: most claim metrics are fractions or counts,
    where 0.01 is close to zero.
    """
    x = np.asarray(baseline, dtype=float)
    mean = float(np.mean(x))
    se = float(np.std(x, ddof=1)) / math.sqrt(x.size) if x.size > 1 else 0.0
    if 0 < tol < se:
        return (
            f"the default tolerance {tol:.3g} (10% of the baseline mean {mean:.3g}) is smaller than the standard "
            f"error of that mean ({se:.3g}); set an absolute tolerance for this claim"
        )
    if abs(mean) < NEAR_ZERO:
        return (
            f"the baseline mean {mean:.3g} is near zero, so the default tolerance (10% of it) is only {tol:.3g}; "
            "set an absolute tolerance for this claim"
        )
    return None


def default_tolerance(baseline: np.ndarray, fraction: float = 0.10) -> float:
    """Default equivalence margin: ``fraction`` of the absolute baseline mean.

    When the mean is zero it falls back to ``fraction`` of the standard deviation, then to ``1e-9``.
    """
    m = abs(float(np.mean(baseline)))
    if m > 0:
        return fraction * m
    sd = float(np.std(baseline, ddof=1)) if baseline.size > 1 else 0.0
    return fraction * sd if sd > 0 else 1e-9
