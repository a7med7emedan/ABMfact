"""Read PhysiCell output into the common schema.

PhysiCell 1.14.2 writes one ``outputNNNNNNNN.xml`` (MultiCellDS) per saved time. It names a cells matrix
(``*_cells.mat``, variable ``cells``: one row per variable, one column per cell, rows described by the
``simplified_data/labels`` elements) and a microenvironment matrix (``*_microenvironment0.mat``, variable
``multiscale_microenvironment``: rows x, y, z, voxel volume, then one row per substrate in ID order; one
column per voxel, x varying fastest). Both are MATLAB v4 files.

With the ``[physicell]`` extra, :mod:`pcdl` reads the files; otherwise the minimal reader here does. Both
give the same tables (a test checks this on real PhysiCell output).
"""

from __future__ import annotations

import importlib.util
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np
from scipy.io import loadmat

from ...records import APOPTOTIC, LIVE, NECROTIC, FieldFrame, RunOutputs, cells_table

APOPTOSIS_MODEL, NECROSIS_MODEL = 100, 101
OUTPUT_FILE = re.compile(r"^output(\d{8})\.xml$")


def output_files(folder: str | Path) -> list[Path]:
    """The saved ``outputNNNNNNNN.xml`` files in time order."""
    folder = Path(folder)
    return sorted((p for p in folder.iterdir() if OUTPUT_FILE.match(p.name)), key=lambda p: p.name)


def _parse(path: Path) -> ET.Element:
    return ET.parse(path).getroot()  # noqa: S314 - output written by the user's own simulation


def _time(root: ET.Element) -> float:
    text = root.findtext(".//metadata/current_time")
    if text is None:
        raise ValueError("no metadata/current_time in output XML")
    return float(text)


def _types(root: ET.Element) -> dict[int, str]:
    out: dict[int, str] = {}
    for t in root.iterfind(".//simplified_data/cell_types/type"):
        out[int(t.get("ID", t.get("type", "0")))] = (t.text or "").strip()
    return out


def _state(dead: np.ndarray, cycle_model: np.ndarray) -> np.ndarray:
    state = np.full(dead.shape, LIVE, dtype=np.int8)
    is_dead = dead > 0.5
    state[is_dead] = NECROTIC
    state[is_dead & (np.rint(cycle_model) == APOPTOSIS_MODEL)] = APOPTOTIC
    return state


def _read_minimal(folder: Path, root: ET.Element) -> tuple[dict[str, np.ndarray], list[FieldFrame]]:
    """One saved time with SciPy only."""
    time = _time(root)
    sd = root.find(".//cellular_information//simplified_data")
    if sd is None:
        raise ValueError("no cellular_information simplified_data in output XML")
    labels = {(lab.text or "").strip(): (int(lab.get("index", "0")), int(lab.get("size", "1")))
              for lab in sd.iterfind("labels/label")}  # fmt: skip
    cells_file = (sd.findtext("filename") or "").strip()
    raw = loadmat(folder / cells_file)["cells"] if cells_file else np.zeros((len(labels), 0))
    n = raw.shape[1]

    def row(name: str, k: int = 0) -> np.ndarray:
        i, _ = labels[name]
        return raw[i + k] if n else np.zeros(0)

    cells = {
        "time": np.full(n, time), "id": row("ID"), "type": row("cell_type"),
        "x": row("position", 0), "y": row("position", 1), "z": row("position", 2),
        "volume": row("total_volume"), "state": _state(row("dead"), row("cycle_model")),
    }  # fmt: skip
    return cells, _fields(folder, root, time)


def _fields(folder: Path, root: ET.Element, time: float) -> list[FieldFrame]:
    dom = root.find(".//microenvironment/domain")
    if dom is None:
        return []
    names = [(int(v.get("ID", "0")), v.get("name", "")) for v in dom.iterfind("variables/variable")]
    data_file = (dom.findtext("data/filename") or "").strip()
    if not names or not data_file:
        return []
    m = loadmat(folder / data_file)["multiscale_microenvironment"]
    xs, ys, zs = (np.unique(m[k]) for k in range(3))
    shape = (len(ys), len(xs)) if len(zs) == 1 else (len(zs), len(ys), len(xs))
    origin = (float(xs[0]), float(ys[0])) if len(zs) == 1 else (float(xs[0]), float(ys[0]), float(zs[0]))

    def step(a: np.ndarray) -> float:
        return float(a[1] - a[0]) if len(a) > 1 else 0.0

    spacing = (step(xs), step(ys)) if len(zs) == 1 else (step(xs), step(ys), step(zs))
    return [FieldFrame(time, name, origin, spacing, m[4 + sid].reshape(shape).copy()) for sid, name in sorted(names)]


Cells = dict[str, np.ndarray]


def _read_pcdl(folder: Path, xml: Path, root: ET.Element, types: dict[int, str]) -> tuple[Cells, list[FieldFrame]]:
    import pcdl

    ts = pcdl.TimeStep(xml.name, output_path=str(folder), graph=False, physiboss=False, settingxml=False,
                       verbose=False)  # fmt: skip
    time = float(ts.get_time())
    df = ts.get_cell_df()
    lookup = {name: i for i, name in types.items()}
    type_idx = np.array([lookup.get(str(nm), -1) for nm in df["cell_type"]], dtype=np.int64)
    if (type_idx < 0).any():
        raise ValueError("pcdl returned a cell type name that is not in the output XML")
    dead = df["dead"].astype(bool).to_numpy()
    model = df["cycle_model"].astype(str).to_numpy()
    state = np.full(len(df), LIVE, dtype=np.int8)
    state[dead] = NECROTIC
    state[dead & (model == "apoptosis_death_model")] = APOPTOTIC
    cells = {
        "time": np.full(len(df), time), "id": df.index.to_numpy(), "type": type_idx,
        "x": df["position_x"].to_numpy(float), "y": df["position_y"].to_numpy(float),
        "z": df["position_z"].to_numpy(float), "volume": df["total_volume"].to_numpy(float), "state": state,
    }  # fmt: skip
    return cells, _fields(folder, root, time)


def read_output(folder: str | Path, reader: str = "auto") -> RunOutputs:
    """Every saved time in a PhysiCell output folder, in the common schema.

    ``reader`` is ``"auto"`` (pcdl when installed), ``"pcdl"`` or ``"minimal"``.
    """
    folder = Path(folder)
    files = output_files(folder)
    if not files:
        raise FileNotFoundError(f"no outputNNNNNNNN.xml files in {folder}")
    use_pcdl = reader == "pcdl" or (reader == "auto" and importlib.util.find_spec("pcdl") is not None)
    parts: list[dict[str, np.ndarray]] = []
    frames: list[FieldFrame] = []
    times: list[float] = []
    type_names: dict[int, str] = {}
    for xml in files:
        root = _parse(xml)
        type_names.update(_types(root))
        if use_pcdl:
            cells, fields = _read_pcdl(folder, xml, root, type_names)
        else:
            cells, fields = _read_minimal(folder, root)
        parts.append(cells)
        frames.extend(fields)
        times.append(float(cells["time"][0]) if cells["time"].size else _time(root))
    table: dict[str, Any] = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    ordered = [type_names[i] for i in sorted(type_names)]
    return RunOutputs(cells=cells_table(table), fields=frames, type_names=ordered, save_times=times)
