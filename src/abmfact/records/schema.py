"""The common output schema every engine's results are collected into.

Cells are one table with a row per cell per saved time. Fields are one frame per substrate per saved
time on a regular grid. Observables are a tidy table of named series. Tables are plain mappings of
column name to NumPy array, so the core needs no data-frame library.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np

CELL_COLUMNS = ("time", "id", "type", "x", "y", "z", "volume", "state")
OBSERVABLE_COLUMNS = ("time", "name", "value")

#: Values of the ``state`` column.
LIVE, APOPTOTIC, NECROTIC = 0, 1, 2

_CELL_DTYPES = {"id": np.int64, "type": np.int64, "state": np.int8}


def empty_cells() -> dict[str, np.ndarray]:
    return {c: np.zeros(0, dtype=_CELL_DTYPES.get(c, np.float64)) for c in CELL_COLUMNS}


def cells_table(rows: Mapping[str, Any]) -> dict[str, np.ndarray]:
    """Build a cells table from columns, filling ``z`` with zeros when absent and checking lengths."""
    n = len(np.asarray(rows["x"]))
    out: dict[str, np.ndarray] = {}
    for c in CELL_COLUMNS:
        if c in rows:
            out[c] = np.asarray(rows[c], dtype=_CELL_DTYPES.get(c, np.float64))
        elif c == "z":
            out[c] = np.zeros(n)
        else:
            raise ValueError(f"cells table needs column {c!r}")
        if out[c].shape != (n,):
            raise ValueError(f"cells column {c!r} has shape {out[c].shape}, expected ({n},)")
    return out


@dataclass
class FieldFrame:
    """One substrate on a regular grid at one time. ``values`` is indexed ``[iy, ix]`` in 2D and
    ``[iz, iy, ix]`` in 3D; ``origin`` is the centre of the first voxel."""

    time: float
    substrate: str
    origin: tuple[float, ...]
    spacing: tuple[float, ...]
    values: np.ndarray


@dataclass
class RunOutputs:
    """Everything one run produced, in the common schema. ``scalars`` holds v0.1-style outputs."""

    cells: dict[str, np.ndarray] = field(default_factory=empty_cells)
    fields: list[FieldFrame] = field(default_factory=list)
    observables: dict[str, np.ndarray] = field(
        default_factory=lambda: {"time": np.zeros(0), "name": np.zeros(0, dtype=object), "value": np.zeros(0)}
    )
    scalars: dict[str, float | list[float]] = field(default_factory=dict)
    type_names: list[str] = field(default_factory=list)
    save_times: list[float] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def times(self) -> list[float]:
        """Saved times: the declared ones, or else every distinct time in the cells table and fields."""
        if self.save_times:
            return list(self.save_times)
        ts = set(np.unique(self.cells["time"]).tolist()) | {f.time for f in self.fields}
        return sorted(float(t) for t in ts)

    def cells_at(self, time: float) -> dict[str, np.ndarray]:
        mask = np.isclose(self.cells["time"], time)
        return {c: v[mask] for c, v in self.cells.items()}

    def field_at(self, substrate: str, time: float) -> FieldFrame | None:
        for f in self.fields:
            if f.substrate == substrate and np.isclose(f.time, time):
                return f
        return None

    def observable(self, name: str) -> tuple[np.ndarray, np.ndarray]:
        mask = self.observables["name"] == name
        return self.observables["time"][mask], self.observables["value"][mask]
