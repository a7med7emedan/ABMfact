"""Read what ABMfact's output steppable writes during a CompuCell3D run into the common output schema.

The steppable (see :data:`abmfact.engines.compucell3d.engine.DRIVER`) writes, in the run's output folder:

* ``meta.json``: the saved Monte Carlo steps, the lattice size and whether the model is 2D or 3D;
* ``cells.csv``: one row per cell per save, with ``mcs, id, type, x, y, z, volume, state``, where ``x, y, z``
  is the centre of mass in lattice sites and ``volume`` the number of sites;
* ``fields_<mcs>.npz``: one array per chemical field per save, indexed ``[x, y, z]``.

Positions, volumes and times are converted with the engine's ``site_length`` (micron per site) and
``mcs_minutes`` (minutes per Monte Carlo step). Both default to 1, which keeps the native units.
CompuCell3D cells have no apoptotic or necrotic state of their own: cells of native projects are reported live,
and cells of models exported from ABM-IR carry the state ABMfact's steppable keeps for them.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from ...records import LIVE, FieldFrame, RunOutputs, cells_table


def read_output(
    folder: str | Path,
    type_names: list[str],
    site_length: float = 1.0,
    mcs_minutes: float = 1.0,
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> RunOutputs:
    """The run's cells and fields in the common schema, with times in minutes and lengths in micron.

    ``type_names`` are the model's cell types in type-id order, without ``Medium``; rows of other types are
    skipped. ``origin`` is the position of the lattice's first corner, so a model exported from ABM-IR reports
    positions in its own frame.
    """
    folder = Path(folder)
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    names = [n for n in type_names if n != "Medium"]
    index = {n: k for k, n in enumerate(names)}
    dims = int(meta.get("dimensions", 2))
    cols: dict[str, list[float]] = {k: [] for k in ("time", "id", "type", "x", "y", "z", "volume", "state")}
    with (folder / "cells.csv").open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["type"] not in index:
                continue
            cols["time"].append(float(row["mcs"]) * mcs_minutes)
            cols["id"].append(int(row["id"]))
            cols["type"].append(index[row["type"]])
            for k, axis in enumerate(("x", "y", "z")):
                cols[axis].append(origin[k] + float(row[axis]) * site_length)
            cols["volume"].append(float(row["volume"]) * site_length**dims)
            cols["state"].append(int(row.get("state") or LIVE))
    cells = cells_table({k: np.asarray(v) for k, v in cols.items()})
    frames: list[FieldFrame] = []
    saved = sorted({float(m) for m in meta.get("saved_mcs", [])})
    for mcs in saved:
        path = folder / f"fields_{int(mcs)}.npz"
        if not path.is_file():
            continue
        with np.load(path) as data:
            for name in data.files:
                values = np.asarray(data[name], dtype=float)
                # Fields are written [x, y, z]; the common schema holds 2D frames as [y, x].
                plane = values[:, :, 0].T if values.ndim == 3 else values.T
                centre = (origin[0] + 0.5 * site_length, origin[1] + 0.5 * site_length)
                frames.append(FieldFrame(mcs * mcs_minutes, name, centre, (site_length, site_length), plane))
    return RunOutputs(cells=cells, fields=frames, type_names=names, save_times=[m * mcs_minutes for m in saved])
