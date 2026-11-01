"""Ensemble mode: many seeds of one setting in one engine, summarised with intervals.

Every observable series is summarised at each save time across the seeds: the mean with its ``1 - 2*alpha``
t-interval (the interval the audit and compare use), the standard deviation and the 5%, 50% and 95% quantiles.
Metrics such as ``last(cells)`` are summarised the same way, with each seed's value kept. Runs go through the
run store, so a larger ensemble later reuses the seeds already run.
"""

from __future__ import annotations

import csv
import platform
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from .. import __version__
from .. import stats as st
from ..claims import ClaimError, Metric
from .batch import Job, JobResult, engine_status, run_batch
from .report import dumps, page, table

QUANTILES = (0.05, 0.5, 0.95)


def summarise(values: Sequence[float], alpha: float) -> dict[str, float | int]:
    """The mean with its interval, the standard deviation and the 5%, 50% and 95% quantiles of ``values``."""
    x = np.asarray(values, dtype=float)
    iv = st.mean_interval(x, alpha)
    q = np.quantile(x, QUANTILES)
    return {
        "n": iv.n,
        "mean": iv.estimate,
        "low": iv.low,
        "high": iv.high,
        "sd": iv.sd,
        "q05": float(q[0]),
        "q50": float(q[1]),
        "q95": float(q[2]),
    }


def series_by_time(outputs: Sequence[Mapping[str, Any]]) -> dict[str, dict[float, list[float]]]:
    """Every observable series in ``outputs`` (each run's scalars), as values per save time.

    A series counts when it has one value per entry of the run's ``time``. Runs with different save times are
    aligned by time, so a time reached by fewer runs has a smaller ``n``.
    """
    out: dict[str, dict[float, list[float]]] = {}
    for scalars in outputs:
        times = scalars.get("time")
        if not isinstance(times, list):
            continue
        for name, series in scalars.items():
            if name == "time" or not isinstance(series, list) or len(series) != len(times):
                continue
            per_time = out.setdefault(name, {})
            for t, v in zip(times, series, strict=True):
                if v is not None and np.isfinite(v):
                    per_time.setdefault(float(t), []).append(float(v))
    return out


@dataclass
class EnsembleResult:
    """The outcome of an ensemble.

    Attributes:
        data: The settings, the engine and its fidelity, run counts, every seed's run id, each observable's
            summary per save time, and each metric's summary with its per-seed values.
    """

    data: dict[str, Any]

    def to_json(self, include_timestamp: bool = True) -> str:
        """The result as JSON; without the timestamp it is the same for the same runs."""
        d = dict(self.data)
        if not include_timestamp:
            d.pop("created", None)
        return dumps(d)

    def rows(self) -> list[list[Any]]:
        """One row per observable and save time: name, time, n, mean, low, high, sd, q05, q50, q95."""
        return [
            [
                name,
                row["time"],
                row["n"],
                row["mean"],
                row["low"],
                row["high"],
                row["sd"],
                row["q05"],
                row["q50"],
                row["q95"],
            ]
            for name, rows in self.data["series"].items()
            for row in rows
        ]

    def write(self, out: str | Path) -> dict[str, Path]:
        """Write ``ensemble.json``, ``ensemble.csv`` and ``report.html`` to ``out``."""
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        paths = {"json": out / "ensemble.json", "csv": out / "ensemble.csv", "report": out / "report.html"}
        paths["json"].write_text(self.to_json(), encoding="utf-8")
        with paths["csv"].open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["observable", "time", "n", "mean", "low", "high", "sd", "q05", "q50", "q95"])
            w.writerows(self.rows())
        paths["report"].write_text(render_html(self), encoding="utf-8")
        return paths


def run_ensemble(
    model: str | Path,
    engine: str = "reference",
    seeds: int = 20,
    *,
    seed_start: int = 0,
    params: Mapping[str, Any] | None = None,
    options: Mapping[str, Any] | None = None,
    metrics: Sequence[str] = (),
    alpha: float = 0.05,
    jobs: int = 1,
    workdir: str | Path | None = None,
    store: str | Path | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> EnsembleResult:
    """Run ``seeds`` seeds of one setting and summarise every observable and metric.

    Args:
        model: An ABM-IR file, or a native project for engines that take one.
        engine: The engine.
        seeds: How many seeds, from ``seed_start``.
        seed_start: The first seed.
        params: Parameters applied in every run.
        options: Engine options.
        metrics: Metric expressions to summarise, such as ``last(cells)``.
        alpha: One-sided level; intervals cover ``1 - 2*alpha`` (90% by default).
        jobs: Runs at once.
        workdir: Where run folders go; a temporary folder by default.
        store: A run store; runs already in it are reused and new ones kept.
        progress: Called with ``(done, total)`` after each run.
    """
    if seeds < 2:
        raise ValueError("an ensemble needs at least 2 seeds")
    if not 0 < alpha < 0.5:
        raise ValueError("alpha must lie between 0 and 0.5")
    parsed = [Metric.parse(m) for m in metrics]
    params, options = dict(params or {}), dict(options or {})
    reason, info = engine_status(engine, model, options)
    if reason is not None:
        raise ValueError(f"engine {engine} cannot run this model: {reason}")
    batch = [Job(engine, seed_start + k, params, options) for k in range(seeds)]
    results = run_batch(model, batch, workdir=workdir, store=store, workers=jobs, progress=progress)
    return summarise_results(
        model,
        engine,
        results,
        info=info,
        params=params,
        options=options,
        metrics=parsed,
        alpha=alpha,
        seed_start=seed_start,
    )


def summarise_results(
    model: str | Path,
    engine: str,
    results: Sequence[JobResult],
    *,
    info: Mapping[str, Any] | None = None,
    params: Mapping[str, Any] | None = None,
    options: Mapping[str, Any] | None = None,
    metrics: Sequence[Metric] = (),
    alpha: float = 0.05,
    seed_start: int = 0,
) -> EnsembleResult:
    """Summarise finished runs of one setting as an ensemble (used by ``run_ensemble`` and by other modes)."""
    good = [r for r in results if r.ok]
    errors = sorted({r.problem for r in results if not r.ok and r.problem})
    outputs = [r.record.outputs.scalars for r in good if r.record and r.record.outputs]
    series = {
        name: [{"time": t, **summarise(v, alpha)} for t, v in sorted(per_time.items())]
        for name, per_time in series_by_time(outputs).items()
    }
    metric_data: dict[str, Any] = {}
    for m in metrics:
        values: list[float] = []
        seeds: list[int] = []
        failed: list[str] = []
        for r in good:
            assert r.record is not None and r.record.outputs is not None
            try:
                values.append(m.evaluate(r.record.outputs.scalars))
                seeds.append(r.job.seed)
            except ClaimError as exc:
                failed.append(str(exc))
        entry: dict[str, Any] = {"values": values, "seeds": seeds}
        if len(values) >= 1:
            entry.update(summarise(values, alpha))
        if failed:
            entry["errors"] = sorted(set(failed))[:3]
        metric_data[m.expr] = entry
    data = {
        "tool": "abmfact",
        "version": __version__,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "environment": {"python": platform.python_version(), "numpy": np.__version__, "platform": platform.system()},
        "ensemble": {
            "model": str(model),
            "engine": engine,
            "params": dict(params or {}),
            "options": dict(options or {}),
            "seeds": len(results),
            "seed_start": seed_start,
            "alpha": alpha,
            "interval": f"{1 - 2 * alpha:.0%} t-interval for the mean",
        },
        "engine": dict(info or {}),
        "runs": {
            "ok": sum(1 for r in good if r.record and r.record.status == "ok"),
            "cached": sum(1 for r in good if r.record and r.record.status == "cached"),
            "failed": len(results) - len(good),
            "errors": errors[:5],
            "wall_time": sum((r.record.wall_time or 0.0) for r in good if r.record),
            "by_seed": {str(r.job.seed): (r.record.run_id if r.record else None) for r in results},
        },
        "series": series,
        "metrics": metric_data,
    }
    return EnsembleResult(data)


def render_html(result: EnsembleResult) -> str:
    """The ensemble as a self-contained HTML page: settings, runs, metrics and every series."""
    d = result.data
    e, runs = d["ensemble"], d["runs"]
    settings = table(
        ["setting", "value"],
        [
            ["model", e["model"]],
            ["engine", e["engine"]],
            ["tier", d["engine"].get("tier", "")],
            ["seeds", f"{e['seeds']} from {e['seed_start']}"],
            ["interval", e["interval"]],
            ["params", dumps(e["params"]).strip()],
            ["options", dumps(e["options"]).strip()],
        ],
    )
    run_rows = [
        ["ok", runs["ok"]],
        ["reused from the store", runs["cached"]],
        ["failed", runs["failed"]],
        ["total wall time (s)", runs["wall_time"]],
    ] + [["error", x] for x in runs["errors"]]
    sections = [("Settings", settings), ("Runs", table(["", ""], run_rows))]
    if d["metrics"]:
        rows = [
            [k, v.get("n", 0), v.get("mean"), v.get("low"), v.get("high"), v.get("sd"), v.get("q05"), v.get("q95")]
            for k, v in d["metrics"].items()
        ]
        sections.append(("Metrics", table(["metric", "n", "mean", "low", "high", "sd", "5%", "95%"], rows)))
    for name, rows in d["series"].items():
        body = table(
            ["time", "n", "mean", "low", "high", "sd", "5%", "50%", "95%"],
            [[r["time"], r["n"], r["mean"], r["low"], r["high"], r["sd"], r["q05"], r["q50"], r["q95"]] for r in rows],
        )
        sections.append((f"Series: {name}", body))
    return page(
        f"Ensemble: {Path(e['model']).name} in {e['engine']}", f"ABMfact {d['version']} · {d['created']}", sections
    )
