"""Convergence under refinement of the time step and, where a model supports it, the voxel size.

For every claim's metric at every parameter point, the model runs at three levels: level ``k`` scales
``dt`` (and the voxel size, when the model declares ``dx_scale``) by ``r**-k`` with ``r = 2``. Each level
runs the audit's seeds, so the estimates are ensemble means with intervals. With ``f3`` the coarsest
and ``f1`` the finest mean, ``e21 = f2 - f1`` and ``e32 = f3 - f2``:

* the convergence ratio ``R = e21 / e32`` says whether the levels converge monotonically (``0 < R < 1``),
  oscillate (``-1 < R < 0``) or diverge (``|R| >= 1``);
* the observed order is ``p = ln(e32 / e21) / ln(r)``;
* the grid convergence index of the fine solution is ``GCI = Fs |e21 / f1| / (r**p - 1)`` with safety
  factor ``Fs = 1.25`` for three levels (Roache, 1994; 1998).

Verdicts compare the change between the two finest levels with a tolerance (the claim's own, or 10% of
the fine mean as in the audit) using the same three-outcome equivalence test as the audit:

* ``CONVERGED``: the change between the two finest levels lies within the tolerance;
* ``NOT_CONVERGED``: the change between levels is established (its intervals exclude zero) and grows with
  refinement, whatever its size; or it lies outside the tolerance; or a level gave non-finite values (the
  solution blew up);
* ``INCONCLUSIVE``: the intervals are too wide to decide (the result says how many seeds would settle it),
  or every seed gave the same value at some level, so noise cannot be told from discretisation error
  (the audit treats such degenerate data the same way); a deterministic model sets ``deterministic``;
* ``NOT_TESTABLE``: the model does not declare ``dt_scale``.

Roache, P. J. (1994). Perspective: a method for uniform reporting of grid refinement studies. *Journal of
Fluids Engineering*, 116(3), 405-413. Roache, P. J. (1998). *Verification and Validation in Computational
Science and Engineering*. Hermosa.
"""

from __future__ import annotations

import json
import math
import os
import platform
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np

from .. import __version__
from .. import stats as st
from ..audit import _clean, _json_default, execute, point_key
from ..config import Audit
from ..knobs import Knobs

VERDICTS = ("CONVERGED", "NOT_CONVERGED", "INCONCLUSIVE", "NOT_TESTABLE")
SEVERITY = {"NOT_CONVERGED": 3, "INCONCLUSIVE": 2, "CONVERGED": 1, "NOT_TESTABLE": 0}


class ConvergenceError(ValueError):
    """The convergence settings are not valid."""


@dataclass(frozen=True)
class ConvergenceSettings:
    """How a convergence study refines the model and estimates the GCI.

    Attributes:
        levels: The number of refinement levels; only 3 is accepted.
        ratio: The refinement ratio ``r`` between levels, greater than 1.
        refine: What to refine: ``dt``, optionally with ``dx``.
        safety_factor: The GCI safety factor ``Fs``.
        deterministic: Whether the model is meant to give the same value for every seed.
    """

    levels: int = 3
    ratio: float = 2.0
    refine: tuple[str, ...] = ("dt", "dx")
    safety_factor: float = 1.25
    deterministic: bool = False

    @classmethod
    def from_mapping(cls, m: Mapping[str, Any] | None) -> ConvergenceSettings:
        """Settings from a mapping such as an audit's ``convergence`` section, using the defaults for missing keys.

        Raises:
            ConvergenceError: A key is unknown or a value is out of range.
        """
        m = dict(m or {})
        unknown = sorted(set(m) - set(cls.__dataclass_fields__))
        if unknown:
            raise ConvergenceError(f"unknown convergence keys: {unknown}")
        s = cls(
            levels=int(m.get("levels", 3)),
            ratio=float(m.get("ratio", 2.0)),
            refine=tuple(m.get("refine", ("dt", "dx"))),
            safety_factor=float(m.get("safety_factor", 1.25)),
            deterministic=bool(m.get("deterministic", False)),
        )
        if s.levels != 3:
            raise ConvergenceError("convergence.levels must be 3 (the GCI uses three levels)")
        if s.ratio <= 1:
            raise ConvergenceError("convergence.ratio must be greater than 1")
        if not set(s.refine) <= {"dt", "dx"} or "dt" not in s.refine:
            raise ConvergenceError("convergence.refine must include dt and may add dx")
        return s


@dataclass(frozen=True)
class GCIEstimate:
    """Richardson-type estimates from three levels. ``gci_fine`` is relative to the fine value."""

    e21: float
    e32: float
    ratio_R: float | None
    kind: str
    order: float | None
    extrapolated: float | None
    gci_fine: float | None

    def to_dict(self) -> dict[str, Any]:
        """The estimate as a plain dictionary."""
        return asdict(self)


def gci(f1: float, f2: float, f3: float, r: float = 2.0, safety_factor: float = 1.25) -> GCIEstimate:
    """Observed order, extrapolated value and GCI from fine ``f1``, medium ``f2`` and coarse ``f3``."""
    e21, e32 = f2 - f1, f3 - f2
    if e21 == 0.0 and e32 == 0.0:
        return GCIEstimate(e21, e32, None, "unchanged", None, f1, 0.0)
    if e32 == 0.0:
        return GCIEstimate(e21, e32, None, "divergent", None, None, None)
    R = e21 / e32
    if abs(R) >= 1.0:
        return GCIEstimate(e21, e32, R, "divergent", None, None, None)
    kind = "monotone" if R > 0 else "oscillatory"
    if e21 == 0.0:
        return GCIEstimate(e21, e32, R, kind, None, f1, 0.0)
    p = math.log(abs(e32 / e21)) / math.log(r)
    denom = r**p - 1.0
    extrapolated = f1 + (f1 - f2) / denom
    rel = abs(e21 / f1) if f1 != 0 else None
    return GCIEstimate(e21, e32, R, kind, p, extrapolated, safety_factor * rel / denom if rel is not None else None)


@dataclass
class ConvergenceResult:
    """The outcome of :func:`converge`.

    Attributes:
        data: The full result: tool and environment, audit, settings, levels, a verdict per claim and a summary.
    """

    data: dict[str, Any]

    @property
    def verdicts(self) -> list[dict[str, Any]]:
        """One row per claim, with its verdict, reason and per-point detail."""
        return list(self.data["verdicts"])

    @property
    def any_not_converged(self) -> bool:
        """Whether any claim has the verdict ``NOT_CONVERGED``."""
        return any(v["verdict"] == "NOT_CONVERGED" for v in self.data["verdicts"])

    def by_claim(self) -> dict[str, str]:
        """Each claim's id mapped to its verdict."""
        return {v["claim"]: v["verdict"] for v in self.data["verdicts"]}

    def to_json(self, include_timestamp: bool = True) -> str:
        """The result as indented JSON with sorted keys; ``include_timestamp=False`` drops ``created``."""
        d = dict(self.data)
        if not include_timestamp:
            d.pop("created", None)
        return json.dumps(d, indent=2, sort_keys=True, allow_nan=False, default=_json_default) + "\n"


def level_knobs(k: int, settings: ConvergenceSettings, refine_dx: bool) -> Knobs:
    """Knobs for level ``k``: ``dt_scale`` is ``ratio**-k``, and so is ``dx_scale`` when ``refine_dx`` is set."""
    scale = settings.ratio ** (-k)
    return Knobs(dt_scale=scale, dx_scale=scale if refine_dx else 1.0)


def _point_verdict(
    vals: list[np.ndarray], tol_claim: float | None, alpha: float, settings: ConvergenceSettings
) -> dict[str, Any]:
    """Verdict for one metric at one point; ``vals`` holds the per-seed values, coarse level first."""
    v3, v2, v1 = vals
    n = int(v1.size)
    levels = [st.mean_interval(v, alpha).to_dict() if np.all(np.isfinite(v)) else None for v in vals]
    out: dict[str, Any] = {"levels": levels}
    bad = [k for k, v in enumerate(vals) if not np.all(np.isfinite(v))]
    if bad:
        out.update(verdict="NOT_CONVERGED", reason=f"non-finite values at level {bad[0]}: the solution blew up")
        return out
    flat = [k for k, v in enumerate(vals) if v.size > 1 and float(np.ptp(v)) == 0.0]
    if flat and not settings.deterministic:
        # As in the audit: with no between-seed variance, sampling noise cannot be told from discretisation
        # error. A model that is meant to be deterministic declares it, and its differences are then exact.
        reason = (
            f"every seed gave the same value at level {flat[0]}, so sampling noise cannot be told from "
            "discretisation error (declare convergence.deterministic for a deterministic model)"
        )
        out.update(verdict="INCONCLUSIVE", reason=reason)
        return out
    f3, f2, f1 = (float(v.mean()) for v in vals)
    est = gci(f1, f2, f3, settings.ratio, settings.safety_factor)
    iv21 = st.diff_interval(v1, v2, alpha)
    iv32 = st.diff_interval(v2, v3, alpha)
    tol = tol_claim if tol_claim is not None else st.default_tolerance(v1)
    eq21 = st.equivalence(iv21, tol)
    out.update(gci=est.to_dict(), e21=iv21.to_dict(), e32=iv32.to_dict(), tolerance=tol)
    established = (iv21.low > 0 or iv21.high < 0) and (iv32.low > 0 or iv32.high < 0)
    if established:
        order = f", observed order {est.order:.2f}" if est.order is not None else ""
        gci_text = f", GCI {100 * est.gci_fine:.2g}%" if est.gci_fine is not None else ""
        detail = f"{est.kind}{order}{gci_text}"
    else:
        detail = "level differences within noise, so no order is estimated"
    out["established"] = established
    if established and abs(iv21.estimate) >= abs(iv32.estimate):
        out.update(verdict="NOT_CONVERGED",
                   reason=f"the change grows with refinement ({iv32.estimate:+.4g}, then {iv21.estimate:+.4g}; "
                          f"{detail})")  # fmt: skip
    elif eq21 == "equivalent":
        out.update(verdict="CONVERGED",
                   reason=f"the two finest levels agree within {tol:.3g} ({detail})")  # fmt: skip
    elif eq21 == "shifted":
        out.update(verdict="NOT_CONVERGED",
                   reason=f"still changes by {iv21.estimate:+.4g} between the two finest levels, beyond the "
                          f"tolerance {tol:.3g} ({detail})")  # fmt: skip
    else:
        gap = abs(tol - abs(iv21.estimate))
        out.update(verdict="INCONCLUSIVE", reason="intervals too wide to decide",
                   seeds_needed=st.seeds_needed(n, iv21.half_width, gap))  # fmt: skip
    return out


def converge(
    audit: Audit,
    settings: ConvergenceSettings | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> ConvergenceResult:
    """Run every claim's metric at three refinement levels and judge convergence."""
    settings = settings or ConvergenceSettings.from_mapping(getattr(audit, "convergence", None))
    b = audit.budget
    support = audit.adapter.support
    seeds = list(range(b.seed_start, b.seed_start + b.seeds))
    refine_dx = "dx" in settings.refine and support.dx_scale
    points_by_claim = {c.id: c.points(audit.params) for c in audit.claims}
    all_points: dict[str, dict[str, Any]] = {}
    for pts in points_by_claim.values():
        for p in pts:
            all_points.setdefault(point_key(p), p)
    levels = [level_knobs(k, settings, refine_dx) for k in range(settings.levels)]
    verdicts: list[dict[str, Any]] = []
    if support.dt_scale:
        jobs = [(f"level{k}", pk, params, s, kn.to_dict()) for k, kn in enumerate(levels)
                for pk, params in all_points.items() for s in seeds]  # fmt: skip
        outputs = execute(audit.adapter, jobs, b.jobs, progress)
    for claim in audit.claims:
        row: dict[str, Any] = {"claim": claim.id, "metric": claim.metric.expr, "points": {}}
        if "convergence" in claim.skip_checks:
            row.update(verdict="SKIPPED", reason=claim.skip_checks["convergence"])
            verdicts.append(row)
            continue
        if not support.dt_scale:
            row.update(verdict="NOT_TESTABLE", reason="the model does not declare dt_scale")
            verdicts.append(row)
            continue
        worst = "CONVERGED"
        for p in points_by_claim[claim.id]:
            pk = point_key(p)
            vals = [np.asarray([claim.metric.evaluate(outputs[(f"level{k}", pk, s)]) for s in seeds], dtype=float)
                    for k in range(settings.levels)]  # fmt: skip
            pv = _point_verdict(vals, claim.tolerance, b.alpha, settings)
            row["points"][pk] = pv
            if SEVERITY[pv["verdict"]] > SEVERITY[worst]:
                worst = pv["verdict"]
        reasons = [f"{v['reason']}" for v in row["points"].values() if v["verdict"] == worst]
        row.update(verdict=worst, reason=reasons[0] if reasons else "")
        needed = [v.get("seeds_needed", 0) for v in row["points"].values()]
        if worst == "INCONCLUSIVE":
            row["seeds_needed"] = max(needed)
        verdicts.append(row)
    data = {
        "schema_version": "1",
        "tool": {"name": "abmfact", "version": __version__},
        "created": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "environment": {"python": platform.python_version(), "numpy": np.__version__, "platform": platform.system()},
        "audit": {
            "title": audit.title,
            "source": os.fspath(audit.source) if audit.source else None,
            "sha256": audit.sha256,
            "model": audit.adapter.describe(),
            "params": audit.params,
            "seeds": len(seeds),
            "alpha": b.alpha,
        },
        "settings": {
            **asdict(settings),
            "refine": list(settings.refine),
            "refined": ["dt", "dx"] if refine_dx else ["dt"],
        },
        "levels": [{"level": k, "dt_scale": kn.dt_scale, "dx_scale": kn.dx_scale} for k, kn in enumerate(levels)],
        "verdicts": verdicts,
        "summary": {v: sum(1 for x in verdicts if x["verdict"] == v) for v in (*VERDICTS, "SKIPPED")},
    }
    return ConvergenceResult(_clean(data))
