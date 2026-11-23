"""Calibrate mode: fit parameters so a model's observables match target data or another engine's ensemble.

A calibration file names the model, engine, the free parameters with their ranges (ABM-IR paths, native paths, or
engine options such as ``option:pressure_gain``), the target, the observables to fit, seeds per evaluation and the
method. The target is one of:

* ``data``: a CSV with columns ``time``, ``observable``, ``value`` and optionally ``sd``;
* ``ensemble``: an ``ensemble.json`` written by ``abmfact ensemble``;
* ``engine``: an engine to run as an ensemble now, with ``seeds``, ``seed_start``, ``params`` and ``options``.

Each evaluation runs the model over its seeds, takes the mean of each observable at the target's times (linear
interpolation between saves), and scores the fit with

    distance = mean over observables and times of ((simulated mean - target) / scale) ** 2,

where ``scale`` is the target's standard deviation (from the data or the target ensemble). Where the target has no
spread, the scale is the range of the target's values for that observable, and the result says so.

Methods: ``bayesopt`` (Bayesian optimisation through bayesian-optimization, MIT) and ``abc-smc`` (approximate Bayesian
computation through pyABC, BSD-3). Others register through the entry point group ``abmfact.calibrators``. Both
libraries are in the optional ``[calibrate]`` extra.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import platform
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from .. import __version__
from .batch import Job, engine_status, run_batch
from .design import DesignError, Parameter, latin_hypercube, setting
from .report import dumps, num, page, table

KEYS = {
    "title",
    "model",
    "engine",
    "options",
    "params",
    "parameters",
    "target",
    "observables",
    "seeds_per_evaluation",
    "seed_start",
    "method",
    "bayesopt",
    "abc_smc",
    "plugin",
    "jobs",
    "alpha",
}
TARGET_KEYS = {"data", "ensemble", "engine", "seeds", "seed_start", "params", "options"}
BOOTSTRAP = 1000


class CalibrationError(ValueError):
    """A calibration file, target or method is not valid."""


# -- targets ---------------------------------------------------------------------------------------------------


@dataclass
class Target:
    """What to match: per observable, the target values at their times and their spread where known.

    Attributes:
        series: ``{observable: (times, values, sd or None)}``.
        source: Where the target came from, with a SHA-256 of any file.
    """

    series: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray | None]]
    source: dict[str, Any] = field(default_factory=dict)

    def features(
        self, observables: Sequence[str]
    ) -> tuple[list[tuple[str, float]], np.ndarray, np.ndarray, dict[str, str]]:
        """The fitted points: labels ``(observable, time)``, target values, scales, and each observable's scale kind."""
        labels: list[tuple[str, float]] = []
        values, scales = [], []
        kinds: dict[str, str] = {}
        for name in observables:
            if name not in self.series:
                raise CalibrationError(f"the target has no observable {name!r}; it has {sorted(self.series)}")
            t, v, sd = self.series[name]
            if sd is not None and np.all(np.isfinite(sd)) and np.all(sd > 0):
                s, kinds[name] = np.asarray(sd, dtype=float), "sd"
            else:
                spread = float(np.nanmax(v) - np.nanmin(v)) if v.size else 0.0
                s = np.full(v.shape, spread if spread > 0 else max(float(np.nanmax(np.abs(v))), 1e-12))
                kinds[name] = "range"
                if sd is not None:
                    # Some times have no spread (such as a fixed starting count): use the range there only.
                    sd = np.asarray(sd, dtype=float)
                    ok = np.isfinite(sd) & (sd > 0)
                    if ok.any():
                        s = np.where(ok, sd, s)
                        kinds[name] = "sd, range where the sd is 0"
            labels += [(name, float(x)) for x in t]
            values.append(np.asarray(v, dtype=float))
            scales.append(s)
        return labels, np.concatenate(values), np.concatenate(scales), kinds


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_target_csv(path: str | Path) -> Target:
    """A target from a CSV with ``time``, ``observable``, ``value`` and optionally ``sd`` columns."""
    path = Path(path)
    rows: dict[str, list[tuple[float, float, float]]] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or not {"time", "observable", "value"} <= set(reader.fieldnames):
            raise CalibrationError(f"{path.name} needs columns time, observable and value (and optionally sd)")
        for r in reader:
            sd = float(r["sd"]) if r.get("sd") not in (None, "") else math.nan
            rows.setdefault(r["observable"], []).append((float(r["time"]), float(r["value"]), sd))
    if not rows:
        raise CalibrationError(f"{path.name} has no rows")
    series: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray | None]] = {}
    for name, items in rows.items():
        items.sort()
        a = np.array(items, dtype=float)
        series[name] = (a[:, 0], a[:, 1], None if np.isnan(a[:, 2]).all() else a[:, 2])
    return Target(series, {"kind": "data", "path": str(path), "sha256": _sha256(path)})


def target_from_ensemble(data: Mapping[str, Any], source: Mapping[str, Any]) -> Target:
    """A target from an ensemble result: each observable's mean and standard deviation at every save time."""
    series: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray | None]] = {}
    for name, rows in data["series"].items():
        series[name] = (
            np.array([r["time"] for r in rows], dtype=float),
            np.array([r["mean"] for r in rows]),
            np.array([r["sd"] for r in rows]),
        )
    return Target(series, {**source, "seeds": data["ensemble"]["seeds"], "seed_start": data["ensemble"]["seed_start"]})


# -- the calibration file --------------------------------------------------------------------------------------


@dataclass
class CalibrationSpec:
    """A parsed calibration.

    Attributes:
        title: A short title.
        model: The ABM-IR file or native project.
        engine: The engine whose parameters are fitted.
        parameters: The free parameters and their ranges.
        target: The target settings (``data``, ``ensemble`` or ``engine``).
        observables: The observables fitted; all the target's by default.
        seeds_per_evaluation: Seeds behind each evaluation's means.
        seed_start: The first seed.
        method: ``bayesopt``, ``abc-smc`` or a registered plug-in.
        settings: The method's settings (the file's ``bayesopt``, ``abc_smc`` or ``plugin`` section).
        params: Fixed parameters for every run.
        options: Fixed engine options.
        jobs: Runs at once.
        alpha: One-sided level for intervals.
        base: The folder relative paths are read from.
    """

    title: str
    model: Path
    engine: str
    parameters: list[Parameter]
    target: dict[str, Any]
    observables: list[str] = field(default_factory=list)
    seeds_per_evaluation: int = 10
    seed_start: int = 0
    method: str = "bayesopt"
    settings: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    jobs: int = 1
    alpha: float = 0.05
    base: Path = field(default_factory=Path.cwd)


def spec_from_mapping(raw: Mapping[str, Any], base: Path, title: str = "calibration") -> CalibrationSpec:
    """A calibration from a parsed mapping, with paths relative to ``base``."""
    unknown = sorted(set(raw) - KEYS)
    if unknown:
        raise CalibrationError(f"unknown calibration keys: {unknown}")
    for key in ("model", "engine", "parameters", "target"):
        if key not in raw:
            raise CalibrationError(f"a calibration needs {key!r}")
    target = dict(raw["target"] or {})
    if set(target) - TARGET_KEYS:
        raise CalibrationError(f"unknown target keys: {sorted(set(target) - TARGET_KEYS)}")
    if sum(k in target for k in ("data", "ensemble", "engine")) != 1:
        raise CalibrationError("the target needs exactly one of data, ensemble or engine")
    method = str(raw.get("method", "bayesopt"))
    section = {"bayesopt": "bayesopt", "abc-smc": "abc_smc"}.get(method, "plugin")
    try:
        params = [Parameter.from_mapping(p) for p in raw["parameters"]]
    except DesignError as exc:
        raise CalibrationError(str(exc)) from exc
    spec = CalibrationSpec(
        title=title,
        model=(base / raw["model"]).resolve(),
        engine=str(raw["engine"]),
        parameters=params,
        target=target,
        observables=[str(o) for o in raw.get("observables") or []],
        seeds_per_evaluation=int(raw.get("seeds_per_evaluation", 10)),
        seed_start=int(raw.get("seed_start", 0)),
        method=method,
        settings=dict(raw.get(section) or {}),
        params=dict(raw.get("params") or {}),
        options=dict(raw.get("options") or {}),
        jobs=int(raw.get("jobs", 1)),
        alpha=float(raw.get("alpha", 0.05)),
        base=base,
    )
    if not spec.parameters:
        raise CalibrationError("a calibration needs at least one free parameter")
    if len({p.name for p in spec.parameters}) != len(spec.parameters):
        raise CalibrationError("each free parameter may appear once")
    if spec.seeds_per_evaluation < 1 or spec.seed_start < 0:
        raise CalibrationError("seeds_per_evaluation must be 1 or more and seed_start 0 or more")
    if any(p.integer for p in spec.parameters):
        raise CalibrationError("integer parameters cannot be calibrated")
    from .sweep import _check_ir_paths

    try:
        _check_ir_paths(spec.model, spec.parameters, spec.params)
    except DesignError as exc:
        raise CalibrationError(str(exc)) from exc
    return spec


def load_calibration(path: str | Path, overrides: Mapping[str, Any] | None = None) -> CalibrationSpec:
    """Read a calibration file; relative paths are resolved against its folder."""
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw.update({k: v for k, v in (overrides or {}).items() if v is not None})
    return spec_from_mapping(raw, path.parent, str(raw.get("title", path.stem)))


def resolve_target(
    spec: CalibrationSpec,
    *,
    workdir: str | Path | None = None,
    store: str | Path | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[Target, dict[str, Any] | None]:
    """The target, running the target engine's ensemble when the file asks for one. Returns ``(target, ensemble)``."""
    t = spec.target
    if "data" in t:
        return load_target_csv(spec.base / t["data"]), None
    if "ensemble" in t:
        path = spec.base / t["ensemble"]
        data = json.loads(path.read_text(encoding="utf-8"))
        return target_from_ensemble(data, {"kind": "ensemble", "path": str(path), "sha256": _sha256(path)}), None
    from .ensemble import run_ensemble

    seeds = int(t.get("seeds", 20))
    ens = run_ensemble(
        spec.model,
        str(t["engine"]),
        seeds,
        seed_start=int(t.get("seed_start", spec.seed_start)),
        params={**spec.params, **dict(t.get("params") or {})},
        options=dict(t.get("options") or {}),
        jobs=spec.jobs,
        workdir=workdir,
        store=store,
        progress=progress,
    )
    if ens.data["runs"]["failed"]:
        raise CalibrationError(f"the target ensemble had failed runs: {ens.data['runs']['errors']}")
    source = {"kind": "engine", "engine": str(t["engine"]), "options": dict(t.get("options") or {})}
    return target_from_ensemble(ens.data, source), ens.data


# -- the problem the methods solve -----------------------------------------------------------------------------


@dataclass
class Evaluation:
    """One evaluated setting: its parameter values, distance, per-seed features and run ids."""

    index: int
    values: dict[str, float]
    distance: float
    per_seed: np.ndarray
    runs: list[str | None]
    errors: list[str]
    method_info: dict[str, Any] = field(default_factory=dict)


class Problem:
    """A calibration problem: simulate a setting over its seeds and score it against the target.

    Methods see the parameters, call :meth:`evaluate` with parameter values, and read :attr:`evaluations`.
    """

    def __init__(
        self,
        spec: CalibrationSpec,
        target: Target,
        *,
        workdir: str | Path | None = None,
        store: str | Path | None = None,
        on_evaluation: Callable[[Evaluation], None] | None = None,
    ):
        self.spec = spec
        self.target = target
        self.observables = spec.observables or sorted(target.series)
        self.labels, self.values, self.scales, self.scale_kinds = target.features(self.observables)
        self.workdir, self.store, self.on_evaluation = workdir, store, on_evaluation
        self.evaluations: list[Evaluation] = []

    @property
    def parameters(self) -> list[Parameter]:
        """The free parameters."""
        return self.spec.parameters

    def features(self, scalars: Mapping[str, Any]) -> np.ndarray:
        """A run's observables at the target's times, interpolated between saves.

        A target time up to half a save interval beyond the run's first or last save takes that save's value, since
        engines differ by a step in where they place their saves (CompuCell3D saves its last Monte Carlo step at
        1439 minutes of a 1440-minute run). Times further outside the run are missing (NaN).
        """
        times = np.asarray(scalars.get("time", []), dtype=float)
        out = np.full(len(self.labels), np.nan)
        if not times.size:
            return out
        slack = 0.5 * float(np.min(np.diff(times))) if times.size > 1 else 0.0
        for i, (name, t) in enumerate(self.labels):
            series = scalars.get(name)
            if isinstance(series, list) and len(series) == times.size and times[0] - slack <= t <= times[-1] + slack:
                out[i] = float(np.interp(t, times, np.asarray(series, dtype=float)))
        return out

    def missing(self) -> list[str]:
        """The fitted points that no run of any evaluation could give, as ``observable at time``."""
        seen = np.zeros(len(self.labels), dtype=bool)
        for e in self.evaluations:
            if e.per_seed.size:
                seen |= np.isfinite(e.per_seed).any(axis=0)
        return [f"{n} at t = {t:g}" for (n, t), ok in zip(self.labels, seen, strict=True) if not ok]

    def score(self, per_seed: np.ndarray) -> float:
        """The distance of the seeds' means from the target; infinite when a fitted point has no value."""
        if per_seed.size == 0:
            return math.inf
        with np.errstate(all="ignore"):
            mean = np.nanmean(per_seed, axis=0)
        if not np.isfinite(mean).all():
            return math.inf
        return float(np.mean(((mean - self.values) / self.scales) ** 2))

    def runs(
        self, params: Mapping[str, Any], options: Mapping[str, Any], label: str
    ) -> tuple[list[Mapping[str, Any]], list[str | None], list[str]]:
        """Run one setting over its seeds: ``(each working run's scalars, run ids, errors)``."""
        jobs = [
            Job(self.spec.engine, self.spec.seed_start + k, params, options, label)
            for k in range(self.spec.seeds_per_evaluation)
        ]
        results = run_batch(self.spec.model, jobs, workdir=self.workdir, store=self.store, workers=self.spec.jobs)
        scalars: list[Mapping[str, Any]] = [
            r.record.outputs.scalars for r in results if r.ok and r.record and r.record.outputs
        ]
        ids = [r.record.run_id if r.record else None for r in results]
        return scalars, ids, sorted({r.problem for r in results if not r.ok and r.problem})

    def evaluate(self, values: Sequence[float], info: Mapping[str, Any] | None = None) -> float:
        """Run the setting ``values`` (one per free parameter) over its seeds and return its distance."""
        index = len(self.evaluations)
        params, options = setting(self.parameters, values, self.spec.params, self.spec.options)
        scalars, ids, errors = self.runs(params, options, f"e{index:04d}")
        per_seed = np.array([self.features(s) for s in scalars]) if scalars else np.empty((0, len(self.labels)))
        d = self.score(per_seed)
        named = {p.name: float(v) for p, v in zip(self.parameters, values, strict=True)}
        self.evaluations.append(Evaluation(index, named, d, per_seed, ids, errors, dict(info or {})))
        if self.on_evaluation is not None:
            self.on_evaluation(self.evaluations[-1])
        return d

    def bootstrap_threshold(self, best: Evaluation, level: float = 0.95, seed: int = 0) -> float:
        """The ``level`` quantile of the best evaluation's distance when its seeds are resampled."""
        rows = best.per_seed
        if rows.shape[0] < 2:
            return best.distance
        rng = np.random.default_rng(seed)
        draws = [self.score(rows[rng.integers(0, rows.shape[0], rows.shape[0])]) for _ in range(BOOTSTRAP)]
        return float(np.quantile(draws, level))


# -- methods ---------------------------------------------------------------------------------------------------


def _edge_warnings(params: Sequence[Parameter], values: Mapping[str, float]) -> list[str]:
    out = []
    for p in params:
        u = float(p.unit(np.array([values[p.name]]))[0])
        if u < 0.02 or u > 0.98:
            out.append(f"{p.name}: the best value {values[p.name]:.4g} is at the edge of its range; widen the range")
    return out


def _edge_interval(problem: Problem, best: Evaluation, i: int, threshold: float, refine: int) -> list[float]:
    """The range of parameter ``i``, others at the best fit, whose distance stays within ``threshold``.

    On each side the nearest known setting outside the threshold (an earlier evaluation on this axis, or else the end
    of the range, which is then evaluated) is bisected towards the best fit ``refine`` times, each step a new
    evaluation.
    """
    p = problem.parameters[i]

    def unit(v: float) -> float:
        return float(p.unit(np.array([v]))[0])

    def value(u: float) -> float:
        return float(p.scaled(np.array([u]))[0])

    u_best = unit(best.values[p.name])
    others = {q.name: best.values[q.name] for q in problem.parameters if q.name != p.name}

    def within(d: float) -> bool:
        return math.isfinite(d) and d <= threshold

    def run(u: float) -> bool:
        values = [best.values[q.name] if q.name != p.name else value(u) for q in problem.parameters]
        return within(problem.evaluate(values, {"phase": "interval", "parameter": p.name}))

    edges = []
    for side in (-1.0, 1.0):
        end = 0.0 if side < 0 else 1.0
        pts = [
            (unit(e.values[p.name]), within(e.distance))
            for e in problem.evaluations
            if all(math.isclose(e.values[k], v, rel_tol=1e-9) for k, v in others.items())
            and (unit(e.values[p.name]) - u_best) * side > 0
        ]
        out = [u for u, ok in pts if not ok]
        outside = min(out, key=lambda u: abs(u - u_best)) if out else None
        inside = max(
            [u_best] + [u for u, ok in pts if ok and (outside is None or abs(u - u_best) < abs(outside - u_best))],
            key=lambda u: abs(u - u_best),
        )
        if outside is None:
            if refine == 0 or any(ok and abs(u - end) < 1e-12 for u, ok in pts) or run(end):
                edges.append(end if refine else inside)
                continue
            outside = end
        for _ in range(refine):
            mid = (inside + outside) / 2
            if run(mid):
                inside = mid
            else:
                outside = mid
        edges.append(inside)
    lo, hi = sorted(edges)
    return [value(lo), value(hi)]


def bayesopt(problem: Problem, settings: Mapping[str, Any]) -> dict[str, Any]:
    """Bayesian optimisation of the distance over the unit cube, with an interval from a bootstrap of the seeds.

    ``settings``: ``n_init`` (Latin hypercube start, default 8), ``n_iter`` (further evaluations, default 16),
    ``kappa`` (exploration of the upper confidence bound, default 2.576), ``refine`` (bisection steps per interval
    edge, default 4) and ``seed``. The optimiser maximises ``-log10(distance)``.

    For each parameter, with the others at the best fit, the interval holds the values whose distance lies within the
    95th percentile of the best fit's distances when its seeds are resampled: settings the seeds cannot tell from the
    best. Its edges are found by bisection, each step a new evaluation.
    """
    try:
        from bayes_opt import BayesianOptimization, acquisition
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError(
            'Bayesian optimisation needs bayesian-optimization: pip install "abmfact[calibrate]"'
        ) from exc
    unknown = sorted(set(settings) - {"n_init", "n_iter", "kappa", "seed", "refine"})
    if unknown:
        raise CalibrationError(f"unknown bayesopt settings: {unknown}")
    n_init, n_iter = int(settings.get("n_init", 8)), int(settings.get("n_iter", 16))
    seed, refine = int(settings.get("seed", 0)), int(settings.get("refine", 4))
    if n_init < 2 or n_iter < 0 or refine < 0:
        raise CalibrationError("bayesopt needs n_init of 2 or more, and n_iter and refine of 0 or more")
    params = problem.parameters
    names = [f"u{i}" for i in range(len(params))]

    def objective(d: float) -> float:
        return -12.0 if not math.isfinite(d) else -math.log10(max(d, 1e-12))

    def scaled(u: Sequence[float]) -> list[float]:
        return [float(p.scaled(np.array([x]))[0]) for p, x in zip(params, u, strict=True)]

    opt = BayesianOptimization(
        f=None,
        pbounds={n: (0.0, 1.0) for n in names},
        verbose=0,
        random_state=seed,
        allow_duplicate_points=True,
        acquisition_function=acquisition.UpperConfidenceBound(kappa=float(settings.get("kappa", 2.576))),
    )
    start = latin_hypercube([Parameter(n, 0.0, 1.0) for n in names], n_init, seed)
    rng = np.random.default_rng(seed + 1)
    tried: list[np.ndarray] = []
    for k, u in enumerate([*start, *([None] * n_iter)]):
        if u is None:
            suggestion = opt.suggest()
            u = np.array([suggestion[n] for n in names])
            if any(np.allclose(u, seen, atol=1e-6) for seen in tried):
                u = rng.random(len(names))  # a repeat teaches nothing new: try somewhere else
        tried.append(np.asarray(u, dtype=float))
        d = problem.evaluate(scaled(u), {"phase": "start" if k < n_init else "search"})
        opt.register(params=dict(zip(names, map(float, u), strict=True)), target=objective(d))
    finite = [e for e in problem.evaluations if math.isfinite(e.distance)]
    if not finite:
        errors = sorted({x for e in problem.evaluations for x in e.errors})[:3]
        raise CalibrationError(
            "no evaluation gave every fitted point; missing: "
            + ", ".join(problem.missing()[:5])
            + (f"; run errors: {errors}" if errors else "")
        )
    best = min(finite, key=lambda e: e.distance)
    searched = len(problem.evaluations)
    warnings = []
    if searched >= 8 and best.index >= searched - max(2, searched // 4):
        warnings.append(
            f"the best fit came at evaluation {best.index + 1} of {searched}; more evaluations may improve it"
        )
    threshold = problem.bootstrap_threshold(best, seed=seed)
    intervals = {p.name: _edge_interval(problem, best, i, threshold, refine) for i, p in enumerate(params)}
    closer = min(
        (e for e in problem.evaluations[searched:] if math.isfinite(e.distance)), key=lambda e: e.distance, default=None
    )
    if closer is not None and closer.distance < best.distance:
        warnings.append(
            f"an interval step found a closer fit (distance {closer.distance:.4g}) than the search did; it "
            "is reported as the best, and the interval was bounded around the search's best"
        )
        best = closer
    warnings += _edge_warnings(params, best.values)
    for p in params:
        lo, hi = intervals[p.name]
        if lo <= p.low or hi >= p.high:
            warnings.append(f"{p.name}: the interval reaches the end of the range, so the fit does not bound it there")
    within = sum(1 for e in problem.evaluations if math.isfinite(e.distance) and e.distance <= threshold)
    return {
        "best": best.values,
        "best_distance": best.distance,
        "best_evaluation": best.index,
        "interval": intervals,
        "interval_meaning": "for each parameter, with the others at the best fit, the values whose distance lies "
        "within the 95th percentile of the best fit's distances under a bootstrap of its seeds; edges located by "
        f"{refine} bisection steps",
        "diagnostics": {
            "evaluations": len(problem.evaluations),
            "search_evaluations": searched,
            "n_init": n_init,
            "n_iter": n_iter,
            "refine": refine,
            "bootstrap_threshold": threshold,
            "within_threshold": within,
            "best_so_far": list(np.minimum.accumulate([e.distance for e in problem.evaluations])),
        },
        "warnings": warnings,
    }


def calibrators() -> dict[str, Callable[[Problem, Mapping[str, Any]], dict[str, Any]]]:
    """Every calibration method: the built-ins and those registered under ``abmfact.calibrators``."""
    from importlib.metadata import entry_points

    from .abc_smc import abc_smc
    from .plugins import builtin_plugins

    found: dict[str, Callable[[Problem, Mapping[str, Any]], dict[str, Any]]] = {
        "bayesopt": bayesopt,
        "abc-smc": abc_smc,
    }
    found.update(builtin_plugins())
    for ep in entry_points(group="abmfact.calibrators"):
        found.setdefault(ep.name, ep.load())
    return found


# -- running and results ---------------------------------------------------------------------------------------


@dataclass
class CalibrationResult:
    """The outcome of a calibration.

    Attributes:
        data: Settings, target, the method's result (best fit or posterior with intervals and diagnostics), and
            every evaluation.
        spec: The calibration.
    """

    data: dict[str, Any]
    spec: CalibrationSpec = field(repr=False)

    def to_json(self, include_timestamp: bool = True) -> str:
        """The result as JSON; without the timestamp it is the same for the same runs."""
        d = dict(self.data)
        if not include_timestamp:
            d.pop("created", None)
        return dumps(d)

    @property
    def best(self) -> dict[str, float]:
        """The calibrated values: the best fit, or the posterior median for ABC-SMC."""
        return dict(self.data["result"]["best"])

    def write(self, out: str | Path) -> dict[str, Path]:
        """Write ``calibration.json``, ``evaluations.csv``, ``report.html``, and the calibrated model or options."""
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        paths = {
            "json": out / "calibration.json",
            "evaluations": out / "evaluations.csv",
            "report": out / "report.html",
        }
        paths["json"].write_text(self.to_json(), encoding="utf-8")
        names = [p.name for p in self.spec.parameters]
        with paths["evaluations"].open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["evaluation", *names, "distance", "seeds", "errors"])
            for e in self.data["evaluations"]:
                w.writerow(
                    [e["index"], *(e["values"][n] for n in names), e["distance"], e["seeds"], "; ".join(e["errors"])]
                )
        paths["report"].write_text(render_html(self), encoding="utf-8")
        best = self.best
        option_values = {p.key: best[p.name] for p in self.spec.parameters if p.kind == "option"}
        if option_values:
            p = out / "calibrated_options.json"
            p.write_text(
                dumps(
                    {
                        "engine": self.spec.engine,
                        "options": {**self.spec.options, **option_values},
                        "interval": {
                            p.key: self.data["result"]["interval"][p.name]
                            for p in self.spec.parameters
                            if p.kind == "option"
                        },
                        "method": self.spec.method,
                        "distance": self.data["result"]["best_distance"],
                        "observables": self.data["fit"]["observables"],
                    }
                ),
                encoding="utf-8",
            )
            paths["options"] = p
        ir_values = {p.key: best[p.name] for p in self.spec.parameters if p.kind == "ir"}
        if ir_values:
            paths["model"] = self._write_model(out / "calibrated.yaml", ir_values)
        return paths

    def _write_model(self, path: Path, values: Mapping[str, float]) -> Path:
        from ..ir import apply_params, load_model, save_model

        model = apply_params(load_model(self.spec.model), dict(values))
        note = (
            f"Calibrated with ABMfact {__version__} ({self.spec.method}) on {self.data['created'][:10]}: "
            + ", ".join(f"{k} = {v:.6g}" for k, v in values.items())
            + f"; distance {self.data['result']['best_distance']:.4g} on "
            + ", ".join(self.data["fit"]["observables"])
            + f" against {self.data['target']['kind']} target"
            + f" {self.data['target'].get('path') or self.data['target'].get('engine')}"
            + (f", with fixed settings {json.dumps(self.spec.params, sort_keys=True)}" if self.spec.params else "")
            + "."
        )
        prov = model.provenance.model_copy(
            update={"notes": (model.provenance.notes + "\n" + note).strip(), "created_with": f"abmfact {__version__}"}
        )
        save_model(model.model_copy(update={"provenance": prov}), path)
        return path


def run_calibration(
    spec: CalibrationSpec,
    *,
    workdir: str | Path | None = None,
    store: str | Path | None = None,
    on_evaluation: Callable[[Evaluation], None] | None = None,
) -> CalibrationResult:
    """Resolve the target, run the method, and collect the result.

    ``on_evaluation`` is called with each evaluation as it finishes.
    """
    reason, info = engine_status(spec.engine, spec.model, spec.options)
    if reason is not None:
        raise CalibrationError(f"engine {spec.engine} cannot run this model: {reason}")
    methods = calibrators()
    if spec.method not in methods:
        raise CalibrationError(f"unknown method {spec.method!r}; available: {sorted(methods)}")
    target, ensemble = resolve_target(spec, workdir=workdir, store=store)
    problem = Problem(spec, target, workdir=workdir, store=store, on_evaluation=on_evaluation)
    result = methods[spec.method](problem, spec.settings)
    return collect(spec, problem, result, info, ensemble)


def collect(
    spec: CalibrationSpec,
    problem: Problem,
    result: Mapping[str, Any],
    info: Mapping[str, Any] | None,
    ensemble: Mapping[str, Any] | None = None,
) -> CalibrationResult:
    """A result from a finished method run."""
    best = min((e for e in problem.evaluations if math.isfinite(e.distance)), key=lambda e: e.distance, default=None)
    fit: dict[str, Any] = {
        "observables": problem.observables,
        "points": len(problem.labels),
        "scale": problem.scale_kinds,
    }
    if best is not None and best.per_seed.size:
        with np.errstate(all="ignore"):
            mean = np.nanmean(best.per_seed, axis=0)
        fit["best_fit"] = [
            {"observable": n, "time": t, "target": float(v), "simulated": float(m), "scale": float(s)}
            for (n, t), v, m, s in zip(problem.labels, problem.values, mean, problem.scales, strict=True)
        ]
    data = {
        "tool": "abmfact",
        "version": __version__,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "environment": {"python": platform.python_version(), "numpy": np.__version__, "platform": platform.system()},
        "calibration": {
            "title": spec.title,
            "model": str(spec.model),
            "engine": spec.engine,
            "method": spec.method,
            "settings": spec.settings,
            "parameters": [{"name": p.name, "low": p.low, "high": p.high, "scale": p.scale} for p in spec.parameters],
            "seeds_per_evaluation": spec.seeds_per_evaluation,
            "seed_start": spec.seed_start,
            "params": spec.params,
            "options": spec.options,
            "distance": "mean over fitted points of ((simulated mean - target) / scale) ** 2",
        },
        "engine": dict(info or {}),
        "target": problem.target.source,
        "fit": fit,
        "result": dict(result),
        "evaluations": [
            {
                "index": e.index,
                "values": e.values,
                "distance": e.distance,
                "seeds": int(e.per_seed.shape[0]),
                "errors": e.errors,
                "runs": e.runs,
                **e.method_info,
            }
            for e in problem.evaluations
        ],
    }
    if ensemble is not None:
        data["target_ensemble"] = {"runs": ensemble["runs"], "ensemble": ensemble["ensemble"]}
    return CalibrationResult(data, spec)


def render_html(result: CalibrationResult) -> str:
    """The calibration as a self-contained HTML page."""
    d = result.data
    c, r = d["calibration"], d["result"]
    sections: list[tuple[str, str]] = []
    if r.get("warnings"):
        sections.append(("Warnings", "<ul>" + "".join(f"<li>{w}</li>" for w in r["warnings"]) + "</ul>"))
    rows = [[p["name"], r["best"][p["name"]], *r["interval"][p["name"]], p["low"], p["high"]] for p in c["parameters"]]
    sections.append(
        (
            "Result",
            table(["parameter", "calibrated", "interval low", "interval high", "range low", "range high"], rows)
            + f"<p class='muted'>Distance {num(r['best_distance'])}. Interval: {r['interval_meaning']}.</p>",
        )
    )
    sections.append(
        (
            "Settings",
            table(
                ["setting", "value"],
                [
                    ["model", c["model"]],
                    ["engine", c["engine"]],
                    ["method", c["method"]],
                    ["target", d["target"].get("kind")],
                    ["observables", ", ".join(d["fit"]["observables"])],
                    ["scale", json.dumps(d["fit"]["scale"])],
                    ["seeds per evaluation", c["seeds_per_evaluation"]],
                    ["evaluations", len(d["evaluations"])],
                ],
            ),
        )
    )
    if "best_fit" in d["fit"]:
        sections.append(
            (
                "Best fit",
                table(
                    ["observable", "time", "target", "simulated", "scale"],
                    [
                        [x["observable"], x["time"], x["target"], x["simulated"], x["scale"]]
                        for x in d["fit"]["best_fit"]
                    ],
                ),
            )
        )
    names = [p["name"] for p in c["parameters"]]
    sections.append(
        (
            "Evaluations",
            table(
                ["#", *names, "distance"],
                [[e["index"], *(e["values"][n] for n in names), e["distance"]] for e in d["evaluations"]],
            ),
        )
    )
    return page(f"Calibration: {c['title']}", f"ABMfact {d['version']} · {d['created']}", sections)
