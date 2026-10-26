"""Read what ABMfact's two loggers write during a Morpheus run into the common output schema.

The cell logger writes one tab-separated file, ``logger_<n>.csv``, with a row per cell per save: ``time``,
``cell.id`` (added by Morpheus), then the logged ``cell.id``, ``cell.type`` (an index into the model's cell
types), ``cell.center.x``, ``cell.center.y`` (and ``cell.center.z`` in 3D) and ``cell.volume`` in lattice
nodes. The field logger writes one file per save, ``logger_<m>_<time>.csv``, with ``time``, ``space.x``,
``space.y`` (and ``space.z``) and one column per field.

Lengths are converted with ``node_length`` (micron per node) and times with ``time_minutes`` (minutes per
Morpheus time unit); both default to 1, which keeps the model's own units. Every cell is reported live:
Morpheus cells have no apoptotic or necrotic state of their own.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import numpy as np

from ...records import LIVE, FieldFrame, RunOutputs, cells_table


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        header = [h.strip().strip('"') for h in next(reader)]
        # Morpheus repeats cell.id when it is also logged; the last column of a name wins, which is the same.
        return [dict(zip(header, (v.strip() for v in row), strict=False)) for row in reader if row]


def read_output(
    folder: str | Path,
    type_names: list[str],
    loggers: tuple[int, int],
    node_length: float = 1.0,
    time_minutes: float = 1.0,
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
    dimensions: int = 2,
) -> RunOutputs:
    """The run's cells and fields in the common schema.

    Args:
        folder: The run's output folder.
        type_names: The model's cell types in declaration order, including the medium.
        loggers: The 1-based positions of ABMfact's cell and field loggers.
        node_length: Micron per lattice node.
        time_minutes: Minutes per Morpheus time unit.
        origin: The position of the lattice's first corner, so translated models report their own frame.
        dimensions: 2 or 3, for converting volumes.
    """
    folder = Path(folder)
    cell_file = folder / f"logger_{loggers[0]}.csv"
    names = list(type_names)
    cols: dict[str, list[float]] = defaultdict(list)
    times: set[float] = set()
    for row in _rows(cell_file):
        t = float(row["time"]) * time_minutes
        times.add(t)
        idx = int(float(row["cell.type"]))
        if not 0 <= idx < len(names):
            continue
        cols["time"].append(t)
        cols["id"].append(int(float(row["cell.id"])))
        cols["type"].append(idx)
        for k, axis in enumerate(("x", "y", "z")):
            v = row.get(f"cell.center.{axis}")
            cols[axis].append(origin[k] + (float(v) if v not in (None, "") else 0.0) * node_length)
        cols["volume"].append(float(row["cell.volume"]) * node_length**dimensions)
        cols["state"].append(LIVE)
    columns = {k: np.asarray(cols.get(k, []), dtype=float) for k in ("time", "id", "type", "x", "y", "z", "volume")}
    columns["state"] = np.asarray(cols.get("state", []), dtype=np.int8)
    cells = cells_table(columns)
    frames: list[FieldFrame] = []
    for path in sorted(folder.glob(f"logger_{loggers[1]}_*.csv")) if loggers[1] else []:
        rows = _rows(path)
        if not rows:
            continue
        fields = [k for k in rows[0] if k not in ("time", "space.x", "space.y", "space.z", "0")]
        xs = np.array([int(float(r["space.x"])) for r in rows])
        ys = np.array([int(float(r["space.y"])) for r in rows])
        t = float(rows[0]["time"]) * time_minutes
        times.add(t)
        for f in fields:
            grid = np.zeros((ys.max() + 1, xs.max() + 1))
            grid[ys, xs] = [float(r[f]) for r in rows]
            centre = (origin[0] + 0.5 * node_length, origin[1] + 0.5 * node_length)
            frames.append(FieldFrame(t, f, centre, (node_length, node_length), grid))
    return RunOutputs(cells=cells, fields=frames, type_names=names, save_times=sorted(times))
