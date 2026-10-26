"""The Morpheus engine.

Everything here runs without Morpheus or Docker, except the tests marked ``morpheus``, which run the two example
models in ``tests/fixtures/morpheus`` and skip with the reason when Morpheus is not available.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from abmfact.engines.base import EngineError
from abmfact.engines.morpheus import MorpheusEngine, MorpheusModel, read_output
from abmfact.engines.morpheus.engine import MODEL_FILE, default_observables
from abmfact.engines.morpheus.model import is_model
from abmfact.engines.recommend import recommend
from abmfact.engines.registry import list_engines
from abmfact.knobs import Knobs

FIX = Path(__file__).parent / "fixtures" / "morpheus"
SORTING, CHEMOTAXIS = FIX / "CellSorting_2D.xml", FIX / "AutocrineChemotaxis.xml"


def test_model_reading():
    m = MorpheusModel.read(SORTING)
    assert m.version == "5.0" and m.title() == "Example-CellSorting-2D"
    assert m.lattice() == (200, 200, 0) and m.dimensions() == 2 and m.stop_time() == 25000
    assert m.cell_types() == ["ct1", "ct2", "medium"] and m.biological_types() == ["ct1", "ct2"]
    assert m.fields() == [] and MorpheusModel.read(CHEMOTAXIS).fields() == ["U"]
    assert is_model(SORTING) and not is_model(FIX / "README.md")


def test_editing_and_loggers(tmp_path):
    m = MorpheusModel.read(SORTING)
    m.set_stop_time(100)
    m.set("Global/Constant[@symbol='b']", 2.5)
    assert m.add_output_loggers(10) == (2, 0)  # the model has one logger already, and no fields
    m.write(tmp_path / "m.xml")
    again = MorpheusModel.read(tmp_path / "m.xml")
    assert again.stop_time() == 100 and again.root.find("Global/Constant[@symbol='b']").get("value") == "2.5"
    names = [lg.get("name") for lg in again.root.iterfind("Analysis/Logger")]
    assert names == [None, "abmfact cells"]
    with pytest.raises(EngineError, match="no element"):
        m.set("Global/Nope", 1)
    c = MorpheusModel.read(CHEMOTAXIS)
    assert c.add_output_loggers(5) == (1, 2)  # no logger of its own; one field


def test_a_model_without_cells_gets_only_the_field_logger(tmp_path):
    m = MorpheusModel.read(CHEMOTAXIS)
    for ct in m.root.findall("CellTypes/CellType"):
        if ct.get("class") != "medium":
            m.root.find("CellTypes").remove(ct)
    assert m.biological_types() == [] and m.add_output_loggers(5) == (0, 1)  # Morpheus refuses cell symbols here
    out = tmp_path / "out"
    out.mkdir()
    (out / "logger_00005.csv").write_text('"time"\t"space.x"\t"space.y"\t"U"\n5\t0\t0\t2\n5\t1\t0\t3\n')
    o = read_output(out, ["medium"], (0, 1))
    assert len(o.cells["time"]) == 0 and o.field_at("U", 5.0).values.tolist() == [[2.0, 3.0]]


def test_bad_models_are_reported(tmp_path):
    with pytest.raises(EngineError, match="not a Morpheus model file"):
        MorpheusModel.read(tmp_path / "missing.xml")
    (tmp_path / "other.xml").write_text("<CompuCell3D/>")
    with pytest.raises(EngineError, match="not a MorpheusML model"):
        MorpheusModel.read(tmp_path / "other.xml")
    (tmp_path / "broken.xml").write_text("<MorpheusModel>")
    with pytest.raises(EngineError, match="not valid XML"):
        MorpheusModel.read(tmp_path / "broken.xml")


def test_engine_is_registered_and_declares_what_it_honours():
    assert "morpheus" in list_engines()
    engine = MorpheusEngine()
    assert engine.capabilities().knobs == ()
    assert engine.refuse(SORTING, Knobs()) == []
    assert any("dt_scale" in r for r in engine.refuse(SORTING, Knobs(dt_scale=0.5)))
    assert engine.refuse(SORTING, Knobs(rng_streams="independent")) == []
    assert engine.refuse("examples/ir/monolayer.yaml", Knobs()) == []  # translated, tier T1
    assert "not a MorpheusML" in engine.refuse(FIX / "README.md", Knobs())[0]


def test_prepare_copies_the_model_and_builds_the_command(tmp_path, monkeypatch):
    engine = MorpheusEngine(save_every=50)
    monkeypatch.setattr(engine, "_route", lambda: ("native", "fake", None))
    monkeypatch.setenv("MORPHEUS_BIN", "/fake/morpheus")
    prep = engine.prepare(SORTING, {"Time/StopTime": 200}, Knobs(), 7, workdir=tmp_path / "w")
    assert prep.argv == ["/fake/morpheus", "-f", MODEL_FILE, "--seed", "7", "--outdir", "output", "--num-threads",
                         "1", "--no-gnuplot"]  # fmt: skip
    copy = MorpheusModel.read(prep.workdir / MODEL_FILE)
    assert copy.stop_time() == 200 and MorpheusModel.read(SORTING).stop_time() == 25000
    assert prep.context["loggers"] == (2, 0) and prep.context["type_names"] == ["ct1", "ct2", "medium"]
    independent = engine.prepare(SORTING, {"Time/StopTime": 200}, Knobs(rng_streams="independent"), 7, tmp_path / "w")
    assert independent.argv[4] != "7"  # the seed is remapped for independent streams
    default = MorpheusEngine()
    monkeypatch.setattr(default, "_route", lambda: ("native", "fake", None))
    fields = default.prepare(CHEMOTAXIS, {}, Knobs(), 1, workdir=tmp_path / "c")
    log = [lg for lg in MorpheusModel.read(fields.workdir / MODEL_FILE).root.iterfind("Analysis/Logger")][-1]
    assert log.get("time-step") == "2500.0"  # a tenth of the example's stop time


def test_prepare_refuses_without_morpheus(monkeypatch):
    engine = MorpheusEngine()
    monkeypatch.setattr(engine, "_route", lambda: ("none", "nothing installed", None))
    assert not engine.availability().ok
    with pytest.raises(EngineError, match="nothing installed"):
        engine.prepare(SORTING, {}, Knobs(), 1)


def write_fake_output(out: Path) -> None:
    out.mkdir(parents=True)
    (out / "logger_2.csv").write_text(
        '"time"\t"cell.id"\t"cell.id"\t"cell.type"\t"cell.center.x"\t"cell.center.y"\t"cell.volume"\n'
        "0\t1\t1\t0\t10\t20\t200\n0\t2\t2\t1\t30\t40\t199\n5\t1\t1\t0\t11\t21\t201\n"
    )
    rows = ['"time"\t"space.x"\t"space.y"\t"U"'] + [f"5\t{x}\t{y}\t{x + 10 * y}" for y in range(2) for x in range(3)]
    (out / "logger_3_00005.csv").write_text("\n".join(rows) + "\n")


def test_output_reader_converts_units_and_layout(tmp_path):
    write_fake_output(tmp_path / "out")
    o = read_output(tmp_path / "out", ["ct1", "ct2", "medium"], (2, 3), node_length=2.0, time_minutes=0.5)
    assert o.cells["time"].tolist() == [0, 0, 2.5] and o.cells["x"].tolist() == [20, 60, 22]
    assert o.cells["volume"].tolist() == [800, 796, 804] and o.cells["type"].tolist() == [0, 1, 0]
    frame = o.field_at("U", 2.5)
    assert frame is not None and frame.values.shape == (2, 3) and frame.values[1, 2] == 12.0  # [y, x]
    assert o.save_times == [0.0, 2.5]


def test_default_observables_and_recommend():
    names = [o["name"] for o in default_observables(MorpheusModel.read(CHEMOTAXIS))]
    assert names[0] == "cells" and names[-1] == "u_mean"
    recs = {r.engine: r for r in recommend(SORTING, {"morpheus": MorpheusEngine()})}
    assert recs["morpheus"].runs and recs["morpheus"].route == "as given" and recs["morpheus"].tier == "T0"
    assert recs["morpheus"].checks == ["determinism", "rng_streams"]


# -- integration: these run Morpheus -----------------------------------------------------------------------


def _engine_or_skip(**kw) -> MorpheusEngine:
    engine = MorpheusEngine(**kw)
    if not engine.availability().ok:
        pytest.skip(f"Morpheus cannot run here: {engine.availability().detail}")
    return engine


@pytest.mark.morpheus
def test_cell_sorting_runs_and_is_deterministic(tmp_path):
    engine = _engine_or_skip(save_every=100)
    params = {"Time/StopTime": 300}
    a = engine.run_once(SORTING, params, 3, Knobs(), workdir=tmp_path / "a")
    assert a.status == "ok", a.error
    b = engine.run_once(SORTING, params, 3, Knobs(), workdir=tmp_path / "b")
    c = engine.run_once(SORTING, params, 4, Knobs(), workdir=tmp_path / "c")
    assert a.outputs.save_times == [0, 100, 200, 300]
    assert np.array_equal(a.outputs.cells["x"], b.outputs.cells["x"])
    assert not np.array_equal(a.outputs.cells["x"], c.outputs.cells["x"])
    assert a.outputs.scalars["cells_ct1"][-1] == 50


@pytest.mark.morpheus
def test_autocrine_chemotaxis_writes_its_field(tmp_path):
    engine = _engine_or_skip(save_every=50)
    rec = engine.run_once(CHEMOTAXIS, {"Time/StopTime": 100}, 1, Knobs(), workdir=tmp_path / "w")
    assert rec.status == "ok", rec.error
    last = rec.outputs.field_at("U", 100.0)
    assert last is not None and last.values.shape == (200, 200) and last.values.max() > 0
    shutil.rmtree(tmp_path / "w")
