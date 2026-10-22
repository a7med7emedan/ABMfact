"""Study models for Gate 2: PhysiCell sample projects run natively, with metrics from their own output.

Configuration comes from environment variables, so audit worker processes see it too:

- ``ABMFACT_GATE2_PHYSICELL``: the PhysiCell 1.14.2 source tree (its ``sample_projects`` are used as is)
- ``ABMFACT_GATE2_CACHE``: where builds, work directories and per-run metrics are kept
- ``ABMFACT_GATE2_HORIZON_<PROJECT>``: the horizon in minutes (default: the shipped end time)
- ``ABMFACT_GATE2_THREADS``: override the shipped thread count (the 1-thread diagnostic only)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import numpy as np

from abmfact.adapters import AdapterError, PythonAdapter
from abmfact.knobs import Knobs

SHIPPED_HORIZON = {"heterogeneity": 64800.0, "biorobots": 2880.0}
NEAR = 50.0  # micron, fixed in PROTOCOL.md


def _frames(output: Path) -> tuple[Any, Any]:
    """Cell tables (pandas) at the start and at the horizon, via pcdl."""
    import pcdl

    files = sorted(p.name for p in output.glob("output*.xml"))
    if len(files) < 2:
        raise AdapterError(f"expected at least two saved times in {output}, found {files}")

    def table(name: str) -> Any:
        ts = pcdl.TimeStep(name, output_path=str(output), graph=False, physiboss=False, settingxml=False, verbose=False)
        return ts.get_cell_df()

    return table(files[0]), table(files[-1])


def heterogeneity_metrics(start: Any, end: Any) -> dict[str, float]:
    live0, live1 = start[~start["dead"].astype(bool)], end[~end["dead"].astype(bool)]
    necrotic = end["dead"].astype(bool) & end["cycle_model"].astype(str).str.startswith("necrosis")
    return {
        "growth": len(live1) / max(len(live0), 1),
        "onco_shift": float(live1["oncoprotein"].mean() - live0["oncoprotein"].mean()) if len(live1) else float("nan"),
        "necrotic_fraction": float(necrotic.sum() / max(len(end), 1)),
        "live_end": float(len(live1)),
    }


def _cargo_stats(df: Any) -> tuple[float, float]:
    from scipy.spatial import cKDTree

    pos = df[["position_x", "position_y"]].to_numpy(float)
    kind = df["cell_type"].astype(str).to_numpy()
    directors, cargo = pos[kind == "director cell"], pos[kind == "cargo cell"]
    if len(directors) == 0 or len(cargo) == 0:
        return float("nan"), float("nan")
    d, _ = cKDTree(directors).query(cargo)
    return float(d.mean()), float((d <= NEAR).mean())


def biorobots_metrics(start: Any, end: Any) -> dict[str, float]:
    d0, n0 = _cargo_stats(start)
    d1, n1 = _cargo_stats(end)
    return {"delivery": d0 - d1, "near_gain": n1 - n0, "cargo_distance_end": d1}


class Gate2Model:
    """``run(params, seed, knobs)`` for one sample project; picklable, configured from the environment."""

    def __init__(self, project: str, metrics: Callable[[Any, Any], dict[str, float]]):
        self.project = project
        self.metrics = metrics

    def __repr__(self) -> str:
        return f"Gate2Model({self.project!r})"

    def __call__(self, params: Mapping[str, Any], seed: int, knobs: Knobs) -> dict[str, float]:
        from abmfact.engines.physicell import PhysiCellEngine

        source = Path(os.environ["ABMFACT_GATE2_PHYSICELL"])
        cache = Path(os.environ["ABMFACT_GATE2_CACHE"])
        horizon = float(os.environ.get(f"ABMFACT_GATE2_HORIZON_{self.project.upper()}", SHIPPED_HORIZON[self.project]))
        threads = os.environ.get("ABMFACT_GATE2_THREADS")
        key = hashlib.sha256(
            json.dumps([self.project, dict(params), seed, knobs.to_dict(), horizon, threads], sort_keys=True).encode()
        ).hexdigest()[:24]
        done = cache / "metrics" / f"{key}.json"
        if done.exists():
            return json.loads(done.read_text())
        end = float(params.get("overall/max_time", horizon))
        engine = PhysiCellEngine(
            toolchain="docker",
            cache_dir=cache,
            threads=int(threads) if threads else None,
            save_interval=end,
            observables=[{"name": "cells", "kind": "cell_count"}],
        )
        project = source / "sample_projects" / self.project
        record = engine.run_once(
            project, {"overall/max_time": horizon, **params}, seed, knobs, workdir=cache / "work" / self.project
        )
        if record.status != "ok":
            raise AdapterError(f"{self.project} seed {seed}: {record.error}")
        work = Path(record.output_paths["output"]).parent
        try:
            out = self.metrics(*_frames(work / "output"))
        finally:
            shutil.rmtree(work, ignore_errors=True)
        out["wall_time"] = float(record.wall_time or np.nan)
        done.parent.mkdir(parents=True, exist_ok=True)
        done.write_text(json.dumps(out), encoding="utf-8")
        return out


SUPPORT = {"dt_scale": True, "dx_scale": True}
heterogeneity = PythonAdapter(
    Gate2Model("heterogeneity", heterogeneity_metrics), supports=SUPPORT, name="heterogeneity"
)
biorobots = PythonAdapter(Gate2Model("biorobots", biorobots_metrics), supports=SUPPORT, name="biorobots")
