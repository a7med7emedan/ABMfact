"""The CompuCell3D engine.

Everything here runs without CompuCell3D or Docker, except the tests marked ``compucell3d``, which run the two
demo projects in ``tests/fixtures/compucell3d`` and skip with the reason when CompuCell3D is not available.
"""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from abmfact.engines.base import EngineError
from abmfact.engines.compucell3d import CC3DProject, CompuCell3DEngine, read_output
from abmfact.engines.compucell3d.engine import DRIVER, ZERO_SEED, default_observables, driver_source
from abmfact.engines.compucell3d.project import is_project
from abmfact.engines.physicell import PhysiCellEngine
from abmfact.engines.recommend import recommend
from abmfact.engines.registry import list_engines
from abmfact.ir import load_model
from abmfact.knobs import Knobs

FIX = Path(__file__).parent / "fixtures" / "compucell3d"
SORTING, DIFFUSION = FIX / "cellsorting", FIX / "diffusion"


@pytest.fixture
def sorting(tmp_path):
    return Path(shutil.copytree(SORTING, tmp_path / "cellsorting"))


def test_project_reading(sorting):
    p = CC3DProject.read(sorting)
    assert p.cc3d_file.name == "cellsorting.cc3d" and p.xml_path.name == "cellsorting.xml"
    assert p.dimensions() == (100, 100, 1) and p.steps() == 10000 and p.seed() is None
    assert p.cell_types() == ["Condensing", "NonCondensing"] and p.fields() == []
    assert [f.name for f in p.python_files()] == ["cellsorting.py", "cellsortingSteppables.py"]
    assert CC3DProject.read(DIFFUSION).fields() == ["FGF"]
    assert is_project(sorting) and not is_project(sorting / "Simulation")


def test_project_editing_keeps_the_rest_of_the_model(sorting):
    p = CC3DProject.read(sorting)
    p.set_seed(42)
    p.set_steps(500)
    p.set("Potts/Temperature", 12.5)
    p.ensure_center_of_mass()
    p.ensure_center_of_mass()
    p.write_xml()
    again = CC3DProject.read(sorting)
    assert again.seed() == 42 and again.steps() == 500
    assert again.root.findtext("Potts/Temperature") == "12.5"
    assert len(again.root.findall("Plugin[@Name='CenterOfMass']")) == 1
    text = again.xml_path.read_text()
    assert "Listing all cell types" in text  # comments survive the round trip
    with pytest.raises(EngineError, match="no element"):
        p.set("Potts/Nope", 1)


def test_bad_projects_are_reported(tmp_path):
    with pytest.raises(EngineError, match="not a CompuCell3D project folder"):
        CC3DProject.read(tmp_path / "missing")
    (tmp_path / "empty").mkdir()
    with pytest.raises(EngineError, match=r"exactly one \.cc3d"):
        CC3DProject.read(tmp_path / "empty")
    broken = Path(shutil.copytree(SORTING, tmp_path / "broken"))
    (broken / "Simulation" / "cellsorting.xml").unlink()
    with pytest.raises(EngineError, match="does not exist"):
        CC3DProject.read(broken)


def test_engine_is_registered_and_declares_what_it_honours():
    assert "compucell3d" in list_engines()
    caps = CompuCell3DEngine().capabilities()
    assert caps.knobs == () and caps.dimensions == (2, 3) and "Potts" in caps.mechanics
    engine = CompuCell3DEngine()
    assert engine.refuse(SORTING, Knobs()) == []
    assert any("dt_scale" in r for r in engine.refuse(SORTING, Knobs(dt_scale=0.5)))
    assert engine.refuse(SORTING, Knobs(rng_streams="independent")) == []  # the seed is remapped
    assert engine.refuse("examples/ir/monolayer.yaml", Knobs()) == []  # translated (tier T1)
    three_d = load_model("examples/ir/monolayer.yaml")
    three_d.space.dimensions = 3
    assert "2D" in engine.refuse(three_d, Knobs())[0]
    assert "not a CompuCell3D project" in engine.refuse(FIX, Knobs())[0]


def test_prepare_copies_the_project_and_sets_only_what_a_run_controls(tmp_path, monkeypatch):
    engine = CompuCell3DEngine(save_every=25)
    monkeypatch.setattr(engine, "_route", lambda: ("native", "fake", None))
    monkeypatch.setenv("CC3D_PYTHON", "/fake/python")
    prep = engine.prepare(SORTING, {"Potts/Steps": 100}, Knobs(), 7, workdir=tmp_path / "w")
    work = prep.workdir
    assert prep.argv == ["/fake/python", DRIVER, "project/cellsorting.cc3d", "output", "25"]
    assert (work / DRIVER).read_text() == driver_source()
    p = CC3DProject.read(work / "project")
    assert p.seed() == 7 and p.steps() == 100 and p.root.find("Plugin[@Name='CenterOfMass']") is not None
    assert CC3DProject.read(SORTING).seed() is None  # the original project is untouched
    assert prep.context["type_names"] == ["Condensing", "NonCondensing"]
    assert [o["name"] for o in prep.context["observables"]] == ["cells", "cells_condensing", "cells_noncondensing"]
    again = engine.prepare(SORTING, {"Potts/Steps": 100}, Knobs(), 7, workdir=tmp_path / "w")
    assert again.request.key() == prep.request.key()
    other = engine.prepare(SORTING, {"Potts/Steps": 100}, Knobs(rng_streams="independent"), 7, tmp_path / "w")
    assert CC3DProject.read(other.workdir / "project").seed() != 7
    zero = engine.prepare(SORTING, {}, Knobs(), 0, workdir=tmp_path / "w")  # 0 would make CompuCell3D use the clock
    assert CC3DProject.read(zero.workdir / "project").seed() == ZERO_SEED and zero.request.seed == 0
    with pytest.raises(EngineError, match="seeds from 0"):
        engine.prepare(SORTING, {}, Knobs(), ZERO_SEED, workdir=tmp_path / "w")
    default = CompuCell3DEngine()
    monkeypatch.setattr(default, "_route", lambda: ("native", "fake", None))
    fields = default.prepare(DIFFUSION, {}, Knobs(), 1, workdir=tmp_path / "d")
    assert fields.argv[-2:] == ["100", "FGF"]  # a tenth of the diffusion demo's 1000 steps, then the field


def test_prepare_refuses_without_compucell3d(monkeypatch):
    engine = CompuCell3DEngine()
    monkeypatch.setattr(engine, "_route", lambda: ("none", "nothing installed", None))
    assert not engine.availability().ok
    with pytest.raises(EngineError, match="nothing installed"):
        engine.prepare(SORTING, {}, Knobs(), 1)


def write_fake_output(out: Path) -> None:
    out.mkdir(parents=True)
    rows = [(0, 1, "Condensing", 10, 20, 0, 25), (0, 2, "NonCondensing", 30, 40, 0, 24), (0, 3, "Medium", 0, 0, 0, 0),
            (10, 1, "Condensing", 11, 21, 0, 26), (10, 2, "NonCondensing", 31, 41, 0, 25)]  # fmt: skip
    with (out / "cells.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["mcs", "id", "type", "x", "y", "z", "volume"])
        w.writerows(rows)
    field = np.zeros((4, 3, 1))
    field[3, 1, 0] = 2.0
    np.savez_compressed(out / "fields_10.npz", FGF=field)
    (out / "meta.json").write_text(json.dumps({"saved_mcs": [0, 10], "lattice": [4, 3, 1], "dimensions": 2}))


def test_output_reader_converts_units_and_layout(tmp_path):
    write_fake_output(tmp_path / "out")
    o = read_output(tmp_path / "out", ["Condensing", "NonCondensing"], site_length=2.0, mcs_minutes=0.5)
    assert o.type_names == ["Condensing", "NonCondensing"] and o.save_times == [0.0, 5.0]
    assert o.cells["x"].tolist() == [20, 60, 22, 62] and o.cells["time"].tolist() == [0, 0, 5, 5]
    assert o.cells["volume"].tolist() == [100, 96, 104, 100]  # sites times site_length squared in 2D
    frame = o.field_at("FGF", 5.0)
    assert frame is not None and frame.values.shape == (3, 4) and frame.values[1, 3] == 2.0  # [y, x]
    assert frame.spacing == (2.0, 2.0) and frame.origin == (1.0, 1.0)
    shifted = read_output(tmp_path / "out", ["Condensing", "NonCondensing"], 2.0, 0.5, origin=(-100.0, -50.0, 0.0))
    assert shifted.cells["x"].tolist() == [-80, -40, -78, -38] and shifted.field_at("FGF", 5.0).origin == (-99.0, -49.0)


def test_recommend_runs_a_project_only_in_its_own_engine():
    engines = {"compucell3d": CompuCell3DEngine(), "physicell": PhysiCellEngine()}
    recs = {r.engine: r for r in recommend(SORTING, engines)}
    assert recs["compucell3d"].runs and recs["compucell3d"].route == "as given" and recs["compucell3d"].tier == "T0"
    assert recs["compucell3d"].checks == ["determinism", "rng_streams"]
    assert not recs["physicell"].runs


def test_default_observables_name_types_and_fields():
    names = [o["name"] for o in default_observables(CC3DProject.read(DIFFUSION))]
    assert names == ["cells", "cells_amoeba", "cells_bacteria", "fgf_mean"]


# -- integration: these run CompuCell3D ------------------------------------------------------------------


def _engine_or_skip(**kw) -> CompuCell3DEngine:
    engine = CompuCell3DEngine(**kw)
    avail = engine.availability()
    if not avail.ok:
        pytest.skip(f"CompuCell3D cannot run here: {avail.detail}")
    return engine


@pytest.mark.compucell3d
def test_sorting_demo_runs_and_is_deterministic(tmp_path):
    engine = _engine_or_skip(save_every=50)
    params = {"Potts/Steps": 200}
    a = engine.run_once(SORTING, params, 3, Knobs(), workdir=tmp_path / "a")
    assert a.status == "ok", a.error
    b = engine.run_once(SORTING, params, 3, Knobs(), workdir=tmp_path / "b")
    c = engine.run_once(SORTING, params, 4, Knobs(), workdir=tmp_path / "c")
    assert a.outputs.save_times == [0, 50, 100, 150, 199]
    assert np.array_equal(a.outputs.cells["x"], b.outputs.cells["x"])
    assert not np.array_equal(a.outputs.cells["x"], c.outputs.cells["x"])
    assert a.outputs.scalars["cells"][0] == a.outputs.scalars["cells"][-1] > 100


@pytest.mark.compucell3d
def test_diffusion_demo_writes_its_field_and_conserves_it(tmp_path):
    engine = _engine_or_skip(save_every=10)
    rec = engine.run_once(DIFFUSION, {"Potts/Steps": 30}, 1, Knobs(), workdir=tmp_path / "w")
    assert rec.status == "ok", rec.error
    first, last = rec.outputs.field_at("FGF", 0.0), rec.outputs.field_at("FGF", 29.0)
    assert first is not None and last is not None and first.values.shape == (55, 55)
    # No decay and no-flux edges: diffusion spreads the pulse without changing its total.
    assert last.values.sum() == pytest.approx(first.values.sum(), rel=1e-4) and last.values.max() < first.values.max()
