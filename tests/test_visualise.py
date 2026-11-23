"""Visualise mode: VTK files for ParaView, plots of mode results, run snapshots, fields and movies."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

from abmfact.cli import main
from abmfact.modes.vtk import cells_vtp, collection, export_vtk, field_vti
from abmfact.records.schema import CELL_COLUMNS, FieldFrame, RunOutputs

ROOT = Path(__file__).parent.parent


def outputs() -> RunOutputs:
    rows = {c: [] for c in CELL_COLUMNS}
    for t in (0.0, 60.0):
        for k in range(3 if t == 0 else 5):
            for c, v in zip(CELL_COLUMNS, (t, k, k % 2, 10.0 * k, -5.0 * k, 0.0, 2000.0, int(k == 4) * 2), strict=True):
                rows[c].append(v)
    cells = {c: np.array(v) for c, v in rows.items()}
    field = [FieldFrame(t, "oxygen", (0.0, 0.0), (10.0, 10.0), np.arange(12.0).reshape(3, 4) + t) for t in (0.0, 60.0)]
    return RunOutputs(cells=cells, fields=field, type_names=["a", "b"], save_times=[0.0, 60.0])


def test_cells_are_written_as_polydata_with_their_columns():
    root = ET.fromstring(cells_vtp(outputs().cells_at(60.0), ["a", "b"]))
    piece = root.find("PolyData/Piece")
    assert piece.get("NumberOfPoints") == "5" and piece.get("NumberOfVerts") == "5"
    arrays = {a.get("Name"): a.text.split() for a in piece.iter("DataArray")}
    assert arrays["type"] == ["0", "1", "0", "1", "0"] and arrays["state"][-1] == "2"
    assert len(arrays["points"]) == 15 and arrays["offsets"] == ["1", "2", "3", "4", "5"]
    assert float(arrays["radius"][0]) == pytest.approx((3 * 2000 / (4 * np.pi)) ** (1 / 3))


def test_fields_are_written_as_image_data_with_x_varying_fastest():
    root = ET.fromstring(field_vti(outputs().fields[0]))
    img = root.find("ImageData")
    assert img.get("WholeExtent") == "0 3 0 2 0 0" and img.get("Spacing") == "10 10 1"
    values = [float(v) for v in img.find("Piece/PointData/DataArray").text.split()]
    assert values[:4] == [0.0, 1.0, 2.0, 3.0]  # the first row of [iy, ix], x running fastest


def test_a_run_exports_as_time_series_collections(tmp_path):
    written = export_vtk(outputs(), tmp_path)
    assert set(written) == {"cells", "field:oxygen"}
    sets = ET.parse(written["cells"]).getroot().findall("Collection/DataSet")
    assert [(d.get("timestep"), d.get("file")) for d in sets] == [("0", "cells_0000.vtp"), ("60", "cells_0001.vtp")]
    for d in sets + ET.parse(written["field:oxygen"]).getroot().findall("Collection/DataSet"):
        ET.parse(tmp_path / d.get("file"))  # every listed file exists and is well-formed
    assert "DataSet" not in collection([])


matplotlib = pytest.importorskip("matplotlib")


def test_a_run_gets_snapshots_fields_and_a_gif(tmp_path):
    from abmfact.modes.visualise import visualise_run

    pytest.importorskip("imageio")
    paths = visualise_run(outputs(), tmp_path, times="all", colour="state", film="gif", vtk=True)
    names = {p.name for p in paths}
    assert {
        "cells_state_t0.png",
        "cells_state_t60.png",
        "field_oxygen_t60.png",
        "cells_state.gif",
        "cells.pvd",
    } <= names
    assert all(p.stat().st_size > 0 for p in paths)
    with pytest.raises(ValueError, match="no saved times"):
        visualise_run(RunOutputs(), tmp_path)


def test_mp4_movies_use_imageio_ffmpeg(tmp_path):
    pytest.importorskip("imageio_ffmpeg")
    from abmfact.modes.visualise import movie

    path = movie(outputs(), tmp_path / "run.mp4")
    assert path.stat().st_size > 1000 and path.read_bytes()[4:8] == b"ftyp"


def test_mode_results_are_plotted(tmp_path):
    from abmfact.modes.visualise import visualise_result

    ensemble = {
        "ensemble": {"engine": "reference", "seeds": 3, "interval": "90% t-interval for the mean"},
        "series": {
            "cells": [
                {"time": t, "mean": 40 + t, "low": 39 + t, "high": 41 + t, "q05": 38 + t, "q95": 42 + t}
                for t in (0, 60)
            ]
        },
    }
    (tmp_path / "ensemble.json").write_text(json.dumps(ensemble))
    assert [p.name for p in visualise_result(tmp_path / "ensemble.json", tmp_path / "e")] == ["ensemble_cells.png"]
    sweep = {
        "sweep": {
            "metrics": ["last(cells)"],
            "design": {"method": "lhs"},
            "parameters": [{"name": "rate", "scale": "log"}],
        },
        "summary": [
            {"values": {"rate": r}, "metrics": {"last(cells)": {"mean": 1.0, "low": 0.5, "high": 1.5}}}
            for r in (0.001, 0.01)
        ],
    }
    (tmp_path / "sweep.json").write_text(json.dumps(sweep))
    assert len(visualise_result(tmp_path / "sweep.json", tmp_path / "s")) == 1
    (tmp_path / "other.json").write_text("{}")
    with pytest.raises(ValueError, match="not an ensemble"):
        visualise_result(tmp_path / "other.json", tmp_path / "o")


def test_the_command_draws_a_run_from_a_store_by_its_id(tmp_path, capsys):
    store = tmp_path / "store"
    assert (
        main(
            [
                "run-model",
                str(ROOT / "examples" / "ir" / "monolayer.yaml"),
                "--params",
                '{"experiment.max_time": 120, "experiment.save_interval": 60}',
                "--store",
                str(store),
                "--out",
                str(tmp_path / "run"),
                "-q",
            ]
        )
        == 0
    )
    run_id = json.loads((tmp_path / "run" / "record.json").read_text())["run_id"]
    assert main(["visualise", run_id[:10], "--store", str(store), "--out", str(tmp_path / "v"), "--vtk"]) == 0
    assert (tmp_path / "v" / "vtk" / "cells.pvd").exists() and any((tmp_path / "v").glob("cells_type_t120.png"))
    assert main(["visualise", str(tmp_path / "run"), "--out", str(tmp_path / "v2"), "-q"]) == 0
    assert main(["visualise", "nothing-here", "--out", str(tmp_path / "v3")]) == 2
