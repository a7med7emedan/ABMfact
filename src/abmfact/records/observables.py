"""Observables computed from the common schema, so every engine's results are measured the same way."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from .schema import LIVE, RunOutputs

if TYPE_CHECKING:
    from ..ir import IRModel, Observable


def _type_index(outputs: RunOutputs, name: str | None) -> int | None:
    if name is None:
        return None
    if name not in outputs.type_names:
        raise KeyError(f"cell type {name!r} is not in this run's outputs")
    return outputs.type_names.index(name)


def value_at(spec: Observable, outputs: RunOutputs, time: float) -> float:
    """The value of one observable at one saved time. Empty selections give NaN for averages."""
    if spec.kind.startswith("substrate_"):
        assert spec.substrate is not None
        frame = outputs.field_at(spec.substrate, time)
        if frame is None:
            raise KeyError(f"no field for {spec.substrate!r} at time {time}")
        if spec.kind == "substrate_min":
            return float(frame.values.min())
        if spec.kind == "substrate_max":
            return float(frame.values.max())
        return float(frame.values.mean())
    cells = outputs.cells_at(time)
    sel = np.ones(cells["x"].size, dtype=bool)
    ti = _type_index(outputs, spec.cell_type)
    if spec.kind == "type_fraction":
        live = cells["state"] == LIVE
        n_live = int(live.sum())
        return float((live & (cells["type"] == ti)).sum() / n_live) if n_live else 0.0
    if ti is not None:
        sel &= cells["type"] == ti
    if spec.kind == "cell_count":
        return float(sel.sum())
    if spec.kind == "dead_count":
        return float((sel & (cells["state"] != LIVE)).sum())
    sel &= cells["state"] == LIVE
    if spec.kind == "live_count":
        return float(sel.sum())
    if not sel.any():
        return float("nan")
    if spec.kind == "mean_position":
        return float(cells[spec.axis][sel].mean())
    if spec.kind == "radius_of_gyration":
        pts = np.stack([cells["x"][sel], cells["y"][sel], cells["z"][sel]], axis=1)
        return float(np.sqrt(((pts - pts.mean(axis=0)) ** 2).sum(axis=1).mean()))
    raise ValueError(f"unknown observable kind {spec.kind!r}")


def compute_observables(model: IRModel, outputs: RunOutputs) -> RunOutputs:
    """Fill ``outputs.observables`` (tidy) and ``outputs.scalars`` (one series per observable)."""
    times = outputs.times()
    t_col: list[float] = []
    n_col: list[str] = []
    v_col: list[float] = []
    for spec in model.observables:
        series = [value_at(spec, outputs, t) for t in times]
        outputs.scalars[spec.name] = series
        t_col.extend(times)
        n_col.extend([spec.name] * len(times))
        v_col.extend(series)
    outputs.observables = {
        "time": np.asarray(t_col, dtype=float),
        "name": np.asarray(n_col, dtype=object),
        "value": np.asarray(v_col, dtype=float),
    }
    outputs.scalars["time"] = list(times)
    return outputs
