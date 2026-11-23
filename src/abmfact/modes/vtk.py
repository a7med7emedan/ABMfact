"""VTK export of a run, for ParaView and other VTK readers, written without any dependency.

Cells at each save time become a ``.vtp`` file (VTK XML PolyData): one point per cell with its id, type, state,
volume and equivalent radius as point data. Each substrate at each save time becomes a ``.vti`` file (VTK XML
ImageData) on its voxel grid. A ``.pvd`` collection per series lists the files by time, so ParaView opens the whole
run as one time series.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from pathlib import Path
from xml.sax.saxutils import quoteattr

import numpy as np

from ..records.schema import FieldFrame, RunOutputs


def _array(name: str, values: np.ndarray, kind: str, components: int = 1) -> str:
    flat = np.asarray(values).ravel()
    text = " ".join(f"{v:.9g}" if kind.startswith("Float") else str(int(v)) for v in flat)
    comps = f' NumberOfComponents="{components}"' if components > 1 else ""
    return f'<DataArray type="{kind}" Name={quoteattr(name)}{comps} format="ascii">{text}</DataArray>'


def equivalent_radius(volume: np.ndarray) -> np.ndarray:
    """The radius of a sphere with ``volume``."""
    return np.cbrt(3.0 * np.asarray(volume, dtype=float) / (4.0 * math.pi))


def cells_vtp(cells: dict[str, np.ndarray], type_names: Sequence[str] = ()) -> str:
    """One save time's cells as VTK XML PolyData: a vertex per cell, with its columns as point data."""
    n = int(cells["x"].size)
    pts = np.column_stack([cells["x"], cells["y"], cells.get("z", np.zeros(n))]) if n else np.empty((0, 3))
    data = [
        _array("id", cells["id"], "Int64"),
        _array("type", cells["type"], "Int32"),
        _array("state", cells["state"], "Int32"),
        _array("volume", cells["volume"], "Float64"),
        _array("radius", equivalent_radius(cells["volume"]), "Float64"),
    ]
    names = f"<!-- types: {', '.join(f'{i}={t}' for i, t in enumerate(type_names))} -->" if type_names else ""
    return (
        '<?xml version="1.0"?>\n<VTKFile type="PolyData" version="0.1" byte_order="LittleEndian">'
        f"{names}<PolyData>"
        f'<Piece NumberOfPoints="{n}" NumberOfVerts="{n}" NumberOfLines="0" NumberOfStrips="0" NumberOfPolys="0">'
        f'<PointData Scalars="type">{"".join(data)}</PointData>'
        f"<Points>{_array('points', pts, 'Float64', 3)}</Points>"
        f"<Verts>{_array('connectivity', np.arange(n), 'Int64')}{_array('offsets', np.arange(1, n + 1), 'Int64')}"
        "</Verts></Piece></PolyData></VTKFile>\n"
    )


def field_vti(frame: FieldFrame) -> str:
    """One substrate frame as VTK XML ImageData, with values at the voxel centres."""
    values = np.asarray(frame.values, dtype=float)
    shape = values.shape[::-1]  # [iy, ix] or [iz, iy, ix] stored; VTK counts x first
    dims = [*shape, 1, 1][:3]
    origin = [*frame.origin, 0.0, 0.0][:3]
    spacing = [*frame.spacing, 1.0, 1.0][:3]
    extent = " ".join(f"0 {d - 1}" for d in dims)
    return (
        '<?xml version="1.0"?>\n<VTKFile type="ImageData" version="0.1" byte_order="LittleEndian">'
        f'<ImageData WholeExtent="{extent}" Origin="{" ".join(f"{o:.9g}" for o in origin)}" '
        f'Spacing="{" ".join(f"{s:.9g}" for s in spacing)}"><Piece Extent="{extent}">'
        f"<PointData Scalars={quoteattr(frame.substrate)}>{_array(frame.substrate, values, 'Float64')}</PointData>"
        "</Piece></ImageData></VTKFile>\n"
    )


def collection(entries: Iterable[tuple[float, str]]) -> str:
    """A ``.pvd`` collection listing ``(time, file)`` pairs."""
    rows = "".join(f'<DataSet timestep="{t:.9g}" group="" part="0" file={quoteattr(f)}/>' for t, f in entries)
    return (
        f'<?xml version="1.0"?>\n<VTKFile type="Collection" version="0.1"><Collection>{rows}</Collection></VTKFile>\n'
    )


def export_vtk(outputs: RunOutputs, folder: str | Path) -> dict[str, Path]:
    """Write every save time's cells and fields under ``folder``; return the ``.pvd`` collections by name."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    cell_entries = []
    for k, t in enumerate(outputs.times()):
        cells = outputs.cells_at(t)
        if cells["x"].size or k == 0:
            name = f"cells_{k:04d}.vtp"
            (folder / name).write_text(cells_vtp(cells, outputs.type_names), encoding="utf-8")
            cell_entries.append((t, name))
    if cell_entries:
        written["cells"] = folder / "cells.pvd"
        written["cells"].write_text(collection(cell_entries), encoding="utf-8")
    by_substrate: dict[str, list[tuple[float, str]]] = {}
    for frame in sorted(outputs.fields, key=lambda f: (f.substrate, f.time)):
        entries = by_substrate.setdefault(frame.substrate, [])
        name = f"field_{_safe(frame.substrate)}_{len(entries):04d}.vti"
        (folder / name).write_text(field_vti(frame), encoding="utf-8")
        entries.append((frame.time, name))
    for sub, entries in by_substrate.items():
        written[f"field:{sub}"] = folder / f"field_{_safe(sub)}.pvd"
        written[f"field:{sub}"].write_text(collection(entries), encoding="utf-8")
    return written


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
