"""Sensitivity mode: Sobol (Saltelli) and Morris indices with bootstrap intervals, through SALib.

A sensitivity file is a sweep file without ``design`` and with a ``sensitivity`` section instead. SALib draws
the points on the unit cube; each is scaled to its parameter's range (linearly or on a log scale), run as a
sweep point with ``seeds_per_point`` seeds, and the point's mean is the model output SALib analyses. So the
indices are for the parameters' distributions as stated: uniform on the range, or log-uniform on a log scale.
Morris effects are per unit of that scaled range.

* **Sobol**: first-order (``S1``) and total (``ST``) indices, and second-order (``S2``) when asked, with 95%
  bootstrap intervals. The design needs ``N * (D + 2)`` points, or ``N * (2D + 2)`` with second order.
* **Morris**: ``mu_star``, ``mu`` and ``sigma`` from ``N`` trajectories over ``levels`` grid levels, with a
  bootstrap interval for ``mu_star``.

The model is stochastic, so each point mean carries seed noise. The noise share, the mean variance between a
point's seeds over the total variance of all runs, says how much of what the indices split up is noise.

SALib (MIT) is an optional extra: ``pip install "abmfact[sensitivity]"``.

Herman, J. and Usher, W. (2017). SALib: an open-source Python library for sensitivity analysis. *Journal of Open
Source Software*, 2(9), 97. Saltelli, A. et al. (2010). Variance based sensitivity analysis of model output.
*Computer Physics Communications*, 181(2), 259-270. Morris, M. D. (1991). Factorial sampling plans for preliminary
computational experiments. *Technometrics*, 33(2), 161-174.
"""

from __future__ import annotations

import csv
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from .design import DesignError, Parameter
from .report import dumps, page, table
from .sweep import SweepResult, SweepSpec, run_sweep, spec_from_mapping

METHODS = ("sobol", "morris")
SOBOL_MIN_N = 512
MORRIS_MIN_TRAJECTORIES = 10
WIDE = 0.1
NOISY = 0.2


@dataclass
class SensitivitySettings:
    """How indices are estimated.

    Attributes:
        method: ``sobol`` or ``morris``.
        n: Sobol base sample size ``N``, or the number of Morris trajectories.
        second_order: Also estimate Sobol second-order indices.
        levels: Morris grid levels (even numbers are recommended).
        num_resamples: Bootstrap resamples for the intervals.
        conf_level: Interval coverage.
        seed: Seed for SALib's sampling and bootstrap.
    """

    method: str = "sobol"
    n: int = 512
    second_order: bool = False
    levels: int = 4
    num_resamples: int = 1000
    conf_level: float = 0.95
    seed: int = 0

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> SensitivitySettings:
        """Settings from a file's ``sensitivity`` section."""
        unknown = sorted(set(raw) - {"method", "n", "second_order", "levels", "num_resamples", "conf_level", "seed"})
        if unknown:
            raise DesignError(f"unknown sensitivity keys: {unknown}")
        s = cls(**{k: raw[k] for k in raw})
        if s.method not in METHODS:
            raise DesignError(f"sensitivity method must be one of {METHODS}, got {s.method!r}")
        if int(s.n) < 2 or int(s.levels) < 2 or int(s.num_resamples) < 10 or not 0 < float(s.conf_level) < 1:
            raise DesignError("n and levels must be 2 or more, num_resamples 10 or more, conf_level in (0, 1)")
        return s


def _salib() -> Any:
    try:
        import SALib.analyze.morris
        import SALib.analyze.sobol
        import SALib.sample.morris
        import SALib.sample.sobol
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError('sensitivity analysis needs SALib: pip install "abmfact[sensitivity]"') from exc
    import SALib

    return SALib


def _problem(params: Sequence[Parameter]) -> dict[str, Any]:
    return {"num_vars": len(params), "names": [p.name for p in params], "bounds": [[0.0, 1.0]] * len(params)}


def sample(params: Sequence[Parameter], settings: SensitivitySettings) -> np.ndarray:
    """The unit-cube points SALib asks for, in its order (rows are points)."""
    import warnings

    salib = _salib()
    problem = _problem(params)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # the power-of-two advice is reported by ``design_warnings``
        if settings.method == "sobol":
            return salib.sample.sobol.sample(
                problem, int(settings.n), calc_second_order=settings.second_order, seed=settings.seed
            )
        return salib.sample.morris.sample(problem, int(settings.n), num_levels=int(settings.levels), seed=settings.seed)


def design_warnings(params: Sequence[Parameter], settings: SensitivitySettings) -> list[str]:
    """Reasons the requested design is too small or badly shaped for stable indices, known before any run."""
    out = []
    n = int(settings.n)
    if settings.method == "sobol":
        if n & (n - 1):
            out.append(f"N = {n} is not a power of two, so the Sobol sequence loses its balance")
        if n < SOBOL_MIN_N:
            out.append(f"N = {n} is below {SOBOL_MIN_N}; Sobol indices from so few points are usually unstable")
        if settings.second_order and n < 2 * SOBOL_MIN_N:
            out.append("second-order indices need a larger N than first-order ones; expect wide intervals")
    else:
        if n < MORRIS_MIN_TRAJECTORIES:
            out.append(f"{n} Morris trajectories; at least {MORRIS_MIN_TRAJECTORIES} are recommended")
        if int(settings.levels) % 2:
            out.append(f"{settings.levels} Morris levels; an even number gives equally probable steps")
    return out


def analyse(
    params: Sequence[Parameter], x_unit: np.ndarray, y: np.ndarray, settings: SensitivitySettings
) -> dict[str, Any]:
    """Indices for outputs ``y`` at unit-cube points ``x_unit`` (as drawn by :func:`sample`)."""
    salib = _salib()
    problem = _problem(params)
    y = np.asarray(y, dtype=float)
    names = [p.name for p in params]
    if not np.isfinite(y).all():
        raise ValueError(f"{int((~np.isfinite(y)).sum())} points have no value; indices need every point")
    if settings.method == "sobol":
        r = salib.analyze.sobol.analyze(
            problem,
            y,
            calc_second_order=settings.second_order,
            num_resamples=int(settings.num_resamples),
            conf_level=settings.conf_level,
            seed=settings.seed,
        )
        out: dict[str, Any] = {
            "indices": {
                n: {
                    "S1": float(r["S1"][i]),
                    "S1_conf": float(r["S1_conf"][i]),
                    "ST": float(r["ST"][i]),
                    "ST_conf": float(r["ST_conf"][i]),
                }
                for i, n in enumerate(names)
            },
        }
        if settings.second_order:
            out["second_order"] = [
                {"pair": [names[i], names[j]], "S2": float(r["S2"][i, j]), "S2_conf": float(r["S2_conf"][i, j])}
                for i in range(len(names))
                for j in range(i + 1, len(names))
            ]
        return out
    r = salib.analyze.morris.analyze(
        problem,
        np.asarray(x_unit),
        y,
        num_resamples=int(settings.num_resamples),
        conf_level=settings.conf_level,
        num_levels=int(settings.levels),
        seed=settings.seed,
    )
    return {
        "indices": {
            n: {
                "mu_star": float(r["mu_star"][i]),
                "mu_star_conf": float(r["mu_star_conf"][i]),
                "mu": float(r["mu"][i]),
                "sigma": float(r["sigma"][i]),
            }
            for i, n in enumerate(names)
        }
    }


def result_warnings(method: str, analysis: Mapping[str, Any], noise_share: float | None) -> list[str]:
    """Reasons the estimated indices should be read with care, found after the runs."""
    out = []
    idx = analysis["indices"]
    if method == "sobol":
        wide = [n for n, v in idx.items() if max(v["S1_conf"], v["ST_conf"]) > WIDE]
        if wide:
            out.append(
                f"interval half-widths above {WIDE} for {', '.join(wide)}: the sample is too small for these indices"
            )
        if sum(v["S1"] for v in idx.values()) > 1.1:
            out.append("first-order indices sum to more than 1, a sign of sampling error")
    if noise_share is not None and noise_share > NOISY:
        out.append(
            f"seed noise is {noise_share:.0%} of the output variance; more seeds per point would sharpen the indices"
        )
    return out


def noise_share(sweep: SweepResult, metric: str) -> float | None:
    """The mean within-point variance of ``metric`` over its total variance across all runs (None with 1 seed)."""
    per_point: dict[str, list[float]] = {}
    for r in sweep.data["runs"]:
        if metric in r["metrics"]:
            per_point.setdefault(r["point"], []).append(r["metrics"][metric])
    within = [np.var(v, ddof=1) for v in per_point.values() if len(v) > 1]
    every = [x for v in per_point.values() for x in v]
    if not within or len(every) < 2 or np.var(every, ddof=1) == 0:
        return None
    return float(np.mean(within) / np.var(every, ddof=1))


@dataclass
class SensitivityResult:
    """The outcome of a sensitivity analysis.

    Attributes:
        data: Settings, warnings, and per metric the indices with intervals and the noise share.
        sweep: The sweep that produced the outputs.
    """

    data: dict[str, Any]
    sweep: SweepResult = field(repr=False)

    def to_json(self, include_timestamp: bool = True) -> str:
        """The result as JSON; without the timestamp it is the same for the same runs."""
        d = dict(self.data)
        if not include_timestamp:
            d.pop("created", None)
        return dumps(d)

    def rows(self) -> list[list[Any]]:
        """One row per metric, parameter and index: metric, parameter, index, value, interval half-width."""
        out = []
        for metric, m in self.data["metrics"].items():
            for name, v in m.get("indices", {}).items():
                keys = [k for k in v if not k.endswith("_conf") and k != "sigma" and k != "mu"]
                for k in keys:
                    out.append([metric, name, k, v[k], v.get(f"{k}_conf")])
                for k in ("mu", "sigma"):
                    if k in v:
                        out.append([metric, name, k, v[k], None])
        return out

    def write(self, out: str | Path) -> dict[str, Path]:
        """Write ``indices.json``, ``indices.csv``, ``report.html``, and the sweep's tables under ``sweep/``."""
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        paths = {"json": out / "indices.json", "csv": out / "indices.csv", "report": out / "report.html"}
        paths["json"].write_text(self.to_json(), encoding="utf-8")
        with paths["csv"].open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["metric", "parameter", "index", "value", "interval_half_width"])
            w.writerows(self.rows())
        paths["report"].write_text(render_html(self), encoding="utf-8")
        self.sweep.write(out / "sweep")
        return paths


def load_sensitivity(
    path: str | Path, overrides: Mapping[str, Any] | None = None
) -> tuple[SweepSpec, SensitivitySettings]:
    """Read a sensitivity file: a sweep file with a ``sensitivity`` section and no ``design``.

    ``overrides`` replaces top-level keys (``jobs``) and sensitivity keys (``method``, ``n``).
    """
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    over = {k: v for k, v in (overrides or {}).items() if v is not None}
    sens = dict(raw.pop("sensitivity", None) or {})
    sens.update({k: over.pop(k) for k in ("method", "n") if k in over})
    raw.update(over)
    if "design" in raw:
        raise DesignError("a sensitivity file draws its own points; remove 'design' and set 'sensitivity'")
    settings = SensitivitySettings.from_mapping(sens)
    raw["design"] = {"method": "lhs", "n": 2}  # a placeholder: the points come from SALib
    spec = spec_from_mapping(raw, path.parent, str(raw.get("title", path.stem)))
    spec.method, spec.n = settings.method, int(settings.n)
    return spec, settings


def run_sensitivity(
    spec: SweepSpec,
    settings: SensitivitySettings,
    *,
    workdir: str | Path | None = None,
    store: str | Path | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> SensitivityResult:
    """Draw SALib's points, run them as a sweep, and estimate indices for every metric."""
    from datetime import UTC, datetime

    from .. import __version__

    if any(p.integer for p in spec.parameters):
        raise DesignError("integer parameters are not supported in sensitivity analysis; use a sweep")
    unit = sample(spec.parameters, settings)
    points = np.stack([p.scaled(unit[:, i]) for i, p in enumerate(spec.parameters)], axis=1)
    sweep = run_sweep(spec, points=points, workdir=workdir, store=store, progress=progress)
    metrics: dict[str, Any] = {}
    for m in spec.metrics:
        y = sweep.values(m.expr)
        share = noise_share(sweep, m.expr)
        try:
            analysis = analyse(spec.parameters, unit, y, settings)
        except ValueError as exc:
            metrics[m.expr] = {"error": str(exc), "noise_share": share}
            continue
        metrics[m.expr] = {
            **analysis,
            "noise_share": share,
            "warnings": result_warnings(settings.method, analysis, share),
        }
    data = {
        "tool": "abmfact",
        "version": __version__,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "sensitivity": {
            "title": spec.title,
            "model": str(spec.model),
            "engine": spec.engine,
            "method": settings.method,
            "n": int(settings.n),
            "second_order": settings.second_order,
            "levels": int(settings.levels),
            "num_resamples": int(settings.num_resamples),
            "conf_level": settings.conf_level,
            "seed": settings.seed,
            "points": len(points),
            "seeds_per_point": spec.seeds_per_point,
            "parameters": sweep.data["sweep"]["parameters"],
            "scale": "indices are for each parameter uniform on its range, or log-uniform on a log scale",
        },
        "warnings": design_warnings(spec.parameters, settings),
        "counts": sweep.data["counts"],
        "metrics": metrics,
    }
    return SensitivityResult(data, sweep)


def render_html(result: SensitivityResult) -> str:
    """The indices as a self-contained HTML page, with every warning first."""
    d = result.data
    s = d["sensitivity"]
    sections: list[tuple[str, str]] = []
    warn = list(d["warnings"]) + [f"{k}: {w}" for k, m in d["metrics"].items() for w in m.get("warnings", [])]
    if warn:
        sections.append(("Warnings", "<ul>" + "".join(f"<li>{w}</li>" for w in warn) + "</ul>"))
    sections.append(
        (
            "Settings",
            table(
                ["setting", "value"],
                [
                    ["model", s["model"]],
                    ["engine", s["engine"]],
                    ["method", s["method"]],
                    ["N", s["n"]],
                    ["points", s["points"]],
                    ["seeds per point", s["seeds_per_point"]],
                    ["bootstrap", s["num_resamples"]],
                    ["interval", f"{s['conf_level']:.0%}"],
                    ["inputs", s["scale"]],
                    [
                        "runs",
                        f"{d['counts']['ok']} new, {d['counts']['cached']} reused, {d['counts']['failed']} failed",
                    ],
                ],
            ),
        )
    )
    for metric, m in d["metrics"].items():
        if "error" in m:
            sections.append((f"Metric {metric}", f"<p>{m['error']}</p>"))
            continue
        if s["method"] == "sobol":
            rows = [[n, v["S1"], v["S1_conf"], v["ST"], v["ST_conf"]] for n, v in m["indices"].items()]
            body = table(["parameter", "S1", "± (S1)", "ST", "± (ST)"], rows)
        else:
            rows = [[n, v["mu_star"], v["mu_star_conf"], v["mu"], v["sigma"]] for n, v in m["indices"].items()]
            body = table(["parameter", "mu*", "± (mu*)", "mu", "sigma"], rows)
        share = m.get("noise_share")
        note = f"<p class='muted'>Seed noise share: {share:.1%}</p>" if share is not None else ""
        sections.append((f"Metric {metric}", body + note))
    return page(f"Sensitivity: {s['title']}", f"ABMfact {d['version']} · {d['created']}", sections)
