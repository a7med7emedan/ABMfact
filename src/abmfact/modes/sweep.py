"""Sweep mode: run one model over a design of parameter settings, each with a few seeds.

A sweep file names the model, engine, varied parameters with their ranges, the design (a full ``grid``, a Latin
hypercube ``lhs`` or a scrambled ``sobol`` sequence), seeds per point and the metrics to record. Every point
runs the same seeds, so differences between points are not blurred by different random streams. Runs go
through the run store: a sweep that is run again, or stopped and restarted, reuses every finished run.

On a cluster, ``backend: slurm`` writes a Slurm job array for the runs not yet in the store. Each array task
runs ``abmfact run-model`` into its own folder; ``collect`` then reads those folders back and summarises them.
"""

from __future__ import annotations

import csv
import json
import platform
import shlex
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from .. import __version__
from ..claims import ClaimError, Metric
from .batch import Job, JobResult, engine_status, run_batch
from .design import DesignError, Parameter, make_design, setting
from .ensemble import summarise
from .report import dumps, num, page, table

KEYS = {"title", "model", "engine", "options", "params", "parameters", "design", "seeds_per_point", "seed_start",
        "metrics", "jobs", "alpha", "slurm"}  # fmt: skip
SLURM_KEYS = {"partition", "time", "mem", "cpus_per_task", "account", "max_parallel", "command"}


@dataclass
class SweepSpec:
    """A parsed sweep.

    Attributes:
        title: A short title.
        model: The ABM-IR file or native project.
        engine: The engine.
        parameters: The varied parameters.
        method: ``grid``, ``lhs`` or ``sobol``.
        n: Points for ``lhs`` and ``sobol``.
        levels: Levels per parameter for ``grid``.
        design_seed: The seed that draws ``lhs`` and ``sobol`` points.
        seeds_per_point: Seeds at every point (the same seeds everywhere).
        seed_start: The first seed.
        params: Fixed parameters for every run.
        options: Fixed engine options.
        metrics: Metrics recorded for every run.
        jobs: Runs at once.
        alpha: One-sided level for intervals.
        slurm: Job-array settings for ``backend: slurm``.
    """

    title: str
    model: Path
    engine: str
    parameters: list[Parameter]
    method: str = "lhs"
    n: int | None = None
    levels: int | list[int] | None = None
    design_seed: int = 0
    seeds_per_point: int = 1
    seed_start: int = 0
    params: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    metrics: list[Metric] = field(default_factory=list)
    jobs: int = 1
    alpha: float = 0.05
    slurm: dict[str, Any] = field(default_factory=dict)

    def points(self) -> np.ndarray:
        """The design: one row per point, one column per parameter."""
        return make_design(self.parameters, self.method, self.n, self.levels, self.design_seed)

    def jobs_for(self, points: np.ndarray) -> list[Job]:
        """Every run: each point with each seed, labelled ``p0000``, ``p0001`` and so on."""
        out = []
        for i, row in enumerate(points):
            run_params, options = setting(self.parameters, row, self.params, self.options)
            out += [Job(self.engine, self.seed_start + k, run_params, options, f"p{i:04d}")
                    for k in range(self.seeds_per_point)]  # fmt: skip
        return out


def _check_ir_paths(model: Path, params: Sequence[Parameter], fixed: Mapping[str, Any]) -> None:
    """Fail early when an ABM-IR parameter path does not exist in the model."""
    from ..ir import apply_params, load_model

    ir = [p for p in params if p.kind == "ir"]
    if not ir or model.is_dir() or model.suffix not in (".yaml", ".yml", ".json"):
        return
    raw = yaml.safe_load(model.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict) or "schema_version" not in raw:
        return
    m = load_model(model)
    try:
        apply_params(m, {**{k: v for k, v in fixed.items() if "/" not in k}, **{p.key: p.low for p in ir}})
    except ValueError as exc:
        raise DesignError(f"a sweep parameter does not fit the model: {exc}") from exc


def load_sweep(path: str | Path, overrides: Mapping[str, Any] | None = None) -> SweepSpec:
    """Read a sweep file; relative paths are resolved against its folder.

    ``overrides`` replaces top-level keys, such as ``jobs`` from the command line.
    """
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw.update({k: v for k, v in (overrides or {}).items() if v is not None})
    return spec_from_mapping(raw, path.parent, str(raw.get("title", path.stem)))


def spec_from_mapping(raw: Mapping[str, Any], base: Path, title: str = "sweep") -> SweepSpec:
    """A sweep from a parsed mapping, with paths relative to ``base``."""
    unknown = sorted(set(raw) - KEYS)
    if unknown:
        raise DesignError(f"unknown sweep keys: {unknown}")
    for key in ("model", "parameters", "design"):
        if key not in raw:
            raise DesignError(f"a sweep needs {key!r}")
    design = dict(raw["design"] or {})
    unknown = sorted(set(design) - {"method", "n", "levels", "seed"})
    if unknown:
        raise DesignError(f"unknown design keys: {unknown}")
    slurm = dict(raw.get("slurm") or {})
    if set(slurm) - SLURM_KEYS:
        raise DesignError(f"unknown slurm keys: {sorted(set(slurm) - SLURM_KEYS)}")
    try:
        metrics = [Metric.parse(m) for m in raw.get("metrics") or []]
    except ClaimError as exc:
        raise DesignError(str(exc)) from exc
    spec = SweepSpec(
        title=title,
        model=(base / raw["model"]).resolve(),
        engine=str(raw.get("engine", "reference")),
        parameters=[Parameter.from_mapping(p) for p in raw["parameters"]],
        method=str(design.get("method", "lhs")),
        n=int(design["n"]) if design.get("n") is not None else None,
        levels=design.get("levels"),
        design_seed=int(design.get("seed", 0)),
        seeds_per_point=int(raw.get("seeds_per_point", 1)),
        seed_start=int(raw.get("seed_start", 0)),
        params=dict(raw.get("params") or {}),
        options=dict(raw.get("options") or {}),
        metrics=metrics,
        jobs=int(raw.get("jobs", 1)),
        alpha=float(raw.get("alpha", 0.05)),
        slurm=slurm,
    )
    if spec.seeds_per_point < 1 or spec.seed_start < 0:
        raise DesignError("seeds_per_point must be 1 or more and seed_start 0 or more")
    if not 0 < spec.alpha < 0.5:
        raise DesignError("alpha must lie between 0 and 0.5")
    if not spec.metrics:
        raise DesignError("a sweep needs at least one metric")
    spec.points()  # checks the design now, not after hours of runs
    _check_ir_paths(spec.model, spec.parameters, spec.params)
    return spec


@dataclass
class SweepResult:
    """The outcome of a sweep.

    Attributes:
        data: The settings, the design points, every run with its metrics, each point's summary per metric
            (mean with interval over its seeds), and run counts.
    """

    data: dict[str, Any]

    def to_json(self, include_timestamp: bool = True) -> str:
        """The result as JSON; without the timestamp it is the same for the same runs."""
        d = dict(self.data)
        if not include_timestamp:
            d.pop("created", None)
        return dumps(d)

    def values(self, metric: str) -> np.ndarray:
        """Each point's mean of ``metric`` over its seeds, in point order (NaN where no run gave it)."""
        return np.array([p["metrics"].get(metric, {}).get("mean", np.nan) for p in self.data["summary"]])

    def write(self, out: str | Path) -> dict[str, Path]:
        """Write ``sweep.json``, ``design.csv``, ``runs.csv``, ``summary.csv`` and ``report.html``."""
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        d = self.data
        names = [p["name"] for p in d["sweep"]["parameters"]]
        metrics = d["sweep"]["metrics"]
        paths = {k: out / f for k, f in (("json", "sweep.json"), ("design", "design.csv"), ("runs", "runs.csv"),
                                          ("summary", "summary.csv"), ("report", "report.html"))}  # fmt: skip
        paths["json"].write_text(self.to_json(), encoding="utf-8")
        with paths["design"].open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["point", *names])
            w.writerows([[p["point"], *(p["values"][n] for n in names)] for p in d["design"]])
        with paths["runs"].open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["point", "seed", "run_id", "status", *metrics, "error"])
            w.writerows([[r["point"], r["seed"], r["run_id"], r["status"], *(r["metrics"].get(m) for m in metrics),
                          r.get("error") or ""] for r in d["runs"]])  # fmt: skip
        with paths["summary"].open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            cols = [f"{m}_{s}" for m in metrics for s in ("n", "mean", "low", "high", "sd")]
            w.writerow(["point", *names, *cols])
            for p in d["summary"]:
                cells = [p["metrics"].get(m, {}).get(s) for m in metrics for s in ("n", "mean", "low", "high", "sd")]
                w.writerow([p["point"], *(p["values"][n] for n in names), *cells])
        paths["report"].write_text(render_html(self), encoding="utf-8")
        return paths


def run_sweep(
    spec: SweepSpec,
    *,
    points: np.ndarray | None = None,
    workdir: str | Path | None = None,
    store: str | Path | None = None,
    progress: Callable[[int, int], None] | None = None,
    collect_from: str | Path | None = None,
) -> SweepResult:
    """Run every point of the design with its seeds and summarise each metric per point.

    Args:
        spec: The sweep.
        points: Run these points instead of the spec's design (rows are points); used by sensitivity analysis.
        workdir: Where run folders go; a temporary folder by default.
        store: A run store; finished runs are reused, new ones kept.
        progress: Called with ``(done, total)`` after each run.
        collect_from: Read the runs that a Slurm job array wrote under this folder instead of running them.
    """
    pts = spec.points() if points is None else np.asarray(points, dtype=float)
    reason, info = engine_status(spec.engine, spec.model, spec.options)
    if reason is not None and collect_from is None:
        raise ValueError(f"engine {spec.engine} cannot run this model: {reason}")
    jobs = spec.jobs_for(pts)
    if collect_from is not None:
        results = _collect(jobs, Path(collect_from), store)
    else:
        results = run_batch(spec.model, jobs, workdir=workdir, store=store, workers=spec.jobs, progress=progress)
    return summarise_sweep(spec, pts, results, info)


def summarise_sweep(spec: SweepSpec, points: np.ndarray, results: Sequence[JobResult],
                    info: Mapping[str, Any] | None = None) -> SweepResult:  # fmt: skip
    """Turn finished runs into a sweep result: per-run metrics and a per-point summary."""
    names = [p.name for p in spec.parameters]
    design: list[dict[str, Any]] = [
        {"point": f"p{i:04d}", "values": dict(zip(names, map(float, row), strict=True))} for i, row in enumerate(points)
    ]
    runs: list[dict[str, Any]] = []
    per_point: dict[str, dict[str, list[float]]] = {p["point"]: {m.expr: [] for m in spec.metrics} for p in design}
    for r in results:
        row: dict[str, Any] = {"point": r.job.label, "seed": r.job.seed, "metrics": {},
                               "run_id": r.record.run_id if r.record else None,
                               "status": r.record.status if r.record else "failed"}  # fmt: skip
        if not r.ok:
            row["status"], row["error"] = "failed", r.problem
        else:
            assert r.record is not None and r.record.outputs is not None
            for m in spec.metrics:
                try:
                    v = m.evaluate(r.record.outputs.scalars)
                except ClaimError as exc:
                    row.setdefault("error", str(exc))
                    continue
                row["metrics"][m.expr] = v
                per_point[r.job.label][m.expr].append(v)
        runs.append(row)
    summary = [{"point": p["point"], "values": p["values"],
                "metrics": {k: summarise(v, spec.alpha) for k, v in per_point[p["point"]].items() if v}}
               for p in design]  # fmt: skip
    ok = [r for r in results if r.ok]
    data = {
        "tool": "abmfact",
        "version": __version__,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "environment": {"python": platform.python_version(), "numpy": np.__version__, "platform": platform.system()},
        "sweep": {
            "title": spec.title,
            "model": str(spec.model),
            "engine": spec.engine,
            "parameters": [
                {"name": p.name, "low": p.low, "high": p.high, "scale": p.scale, "integer": p.integer}
                for p in spec.parameters
            ],
            "design": {
                "method": spec.method,
                "n": spec.n,
                "levels": spec.levels,
                "seed": spec.design_seed,
                "points": len(design),
            },
            "seeds_per_point": spec.seeds_per_point,
            "seed_start": spec.seed_start,
            "params": spec.params,
            "options": spec.options,
            "metrics": [m.expr for m in spec.metrics],
            "alpha": spec.alpha,
        },
        "engine": dict(info or {}),
        "counts": {
            "runs": len(results),
            "ok": sum(1 for r in ok if r.record and r.record.status == "ok"),
            "cached": sum(1 for r in ok if r.record and r.record.status == "cached"),
            "failed": len(results) - len(ok),
            "errors": sorted({r.problem for r in results if not r.ok and r.problem})[:5],
            "wall_time": sum((r.record.wall_time or 0.0) for r in ok if r.record),
        },
        "design": design,
        "runs": runs,
        "summary": summary,
    }
    return SweepResult(data)


# -- Slurm ---------------------------------------------------------------------------------------------------


def _job_dir(root: Path, job: Job) -> Path:
    return root / "jobs" / (job.label or "run") / f"seed-{job.seed}"


def _run_model_command(spec: SweepSpec, job: Job, folder: Path) -> list[str]:
    base = shlex.split(str(spec.slurm.get("command", "abmfact")))
    argv = [*base, "run-model", str(spec.model), "--engine", job.engine, "--seed", str(job.seed),
            "--out", str(folder), "-q"]  # fmt: skip
    if job.params:
        argv += ["--params", json.dumps(dict(job.params), sort_keys=True)]
    for k, v in sorted(job.options.items()):
        argv += ["--option", f"{k}={json.dumps(v)}"]
    return argv


def write_slurm(spec: SweepSpec, out: str | Path, store: str | Path | None = None, submit: bool = False,
                points: np.ndarray | None = None) -> tuple[Path | None, int, int]:  # fmt: skip
    """Write a Slurm job array for every run not yet in ``store``; submit it only when asked.

    Returns ``(script or None, runs written, runs already stored)``. Each task runs ``abmfact run-model`` (or
    ``slurm.command``) into ``out/jobs/<point>/seed-<n>``; collect them with ``run_sweep(..., collect_from=out)``.
    """
    from ..engines.base import PreparedRun
    from ..knobs import Knobs
    from ..records.store import RunStore
    from ..runtime.slurm import SlurmBackend
    from .batch import make_engine

    out = Path(out).resolve()
    run_store = RunStore(store) if store else None
    engines: dict[str, Any] = {}
    todo: list[PreparedRun] = []
    cached = 0
    for job in spec.jobs_for(spec.points() if points is None else points):
        folder = _job_dir(out, job)
        key = json.dumps(dict(job.options), sort_keys=True)
        engine = engines.setdefault(key, make_engine(job.engine, job.options))
        prepared = engine.prepare(str(spec.model), dict(job.params), Knobs(), job.seed, folder / "work")
        if run_store is not None and run_store.get(prepared.request) is not None:
            cached += 1
            continue
        folder.mkdir(parents=True, exist_ok=True)
        todo.append(PreparedRun(job.engine, prepared.request, workdir=folder,
                                argv=_run_model_command(spec, job, folder / "run")))  # fmt: skip
    if not todo:
        return None, 0, cached
    settings = {k: v for k, v in spec.slurm.items() if k != "command"}
    backend = SlurmBackend(job_name=f"abmfact-{spec.title}"[:40], **settings)
    backend.submit_array(todo, out / "sweep.sbatch", submit=submit)
    return out / "sweep.sbatch", len(todo), cached


def _collect(jobs: Sequence[Job], root: Path, store: str | Path | None) -> list[JobResult]:
    from ..records.store import RunStore, read_run

    run_store = RunStore(store) if store else None
    results = []
    for job in jobs:
        folder = _job_dir(root.resolve(), job) / "run"
        if not (folder / "record.json").exists():
            results.append(JobResult(job, error=f"not finished: no record.json in {folder}"))
            continue
        record = read_run(folder)
        if run_store is not None and record.status == "ok" and record.outputs is not None:
            outputs = record.outputs
            record = run_store.put(record)
            record.outputs = outputs
        results.append(JobResult(job, record))
    return results


def render_html(result: SweepResult) -> str:
    """The sweep as a self-contained HTML page: settings, run counts and each point's summary."""
    d = result.data
    s, c = d["sweep"], d["counts"]
    names = [p["name"] for p in s["parameters"]]
    params = table(["parameter", "low", "high", "scale"], [[p["name"], p["low"], p["high"], p["scale"]]
                                                          for p in s["parameters"]])  # fmt: skip
    settings = table(
        ["setting", "value"],
        [
            ["model", s["model"]],
            ["engine", s["engine"]],
            ["design", f"{s['design']['method']}, {s['design']['points']} points"],
            ["seeds per point", f"{s['seeds_per_point']} from {s['seed_start']}"],
            ["fixed params", dumps(s["params"]).strip()],
            ["options", dumps(s["options"]).strip()],
        ],
    )
    counts = table(["", ""], [["runs", c["runs"]], ["new", c["ok"]], ["reused from the store", c["cached"]],
                              ["failed", c["failed"]], ["total wall time (s)", c["wall_time"]]]
                   + [["error", e] for e in c["errors"]])  # fmt: skip
    rows = []
    for p in d["summary"]:
        cells: list[Any] = [p["point"], *(p["values"][n] for n in names)]
        for m in s["metrics"]:
            x = p["metrics"].get(m)
            cells.append(f"{num(x['mean'])} ({num(x['low'])} to {num(x['high'])})" if x else "no runs")
        rows.append(cells)
    summary = table(["point", *names, *s["metrics"]], rows)
    return page(f"Sweep: {s['title']}", f"ABMfact {d['version']} · {d['created']}",
                [("Settings", settings), ("Parameters", params), ("Runs", counts), ("Points", summary)])  # fmt: skip
