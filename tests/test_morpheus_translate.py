"""Translation between ABM-IR and Morpheus models; only the tests marked ``morpheus`` run Morpheus."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from abmfact.engines.morpheus import MorpheusEngine, MorpheusModel
from abmfact.engines.morpheus.translate import (
    ADHESION_REF,
    J_MEDIUM,
    _arith,
    _hill_expr,
    export_model,
    import_model,
    symbol,
)
from abmfact.engines.recommend import recommend
from abmfact.ir import load_model
from abmfact.knobs import Knobs

ROOT = Path(__file__).parent.parent
FIX = ROOT / "tests" / "fixtures" / "morpheus"


def exported(name: str, tmp_path: Path, **kw):
    model = load_model(ROOT / "examples" / "ir" / f"{name}.yaml")
    report = export_model(model, tmp_path / name, **kw)
    return model, report, ET.parse(tmp_path / name / "model.xml").getroot()


def status(report) -> dict[str, str]:
    return {e.path: e.status for e in report.entries}


def test_export_writes_a_model_morpheus_can_read(tmp_path):
    _, report, root = exported("monolayer", tmp_path)
    m = MorpheusModel.read(tmp_path / "monolayer" / "model.xml")
    assert m.biological_types() == ["epithelial"] and m.cell_types()[-1] == "medium"
    assert m.lattice() == (250, 250, 0) and m.stop_time() == 2880  # 500 micron at 2 micron per node
    assert [c.get("type") for c in root.iterfind("Space/Lattice/BoundaryConditions/Condition")] == ["periodic"] * 2
    ct = root.find("CellTypes/CellType[@name='epithelial']")
    r = (3 * 2494 / (4 * math.pi)) ** (1 / 3)
    assert float(ct.find("VolumeConstraint").get("target")) == round(math.pi * r * r / 4)
    assert float(ct.find("Constant[@symbol='base_r_cycle']").get("value")) == pytest.approx(0.00139)
    assert "contact / max(perimeter, 1)" in ct.findtext("Function[@symbol='r_cycle']/Expression")
    assert status(report)["cell_types[epithelial].rules"] == "exact"
    assert status(report)["units"] == "approximated"


def test_hill_combination_matches_physicell():
    rules = [{"signal": "o2", "direction": "increases", "saturation": 0.01, "half_max": 2.0, "hill": 2.0,
              "apply_to_dead": True},
             {"signal": "p", "direction": "decreases", "saturation": 0.0, "half_max": 1.0, "hill": 1.0,
              "apply_to_dead": True}]  # fmt: skip
    expr = _hill_expr(0.001, rules, {"o2": "o2", "p": "p"})
    o2, p = 3.0, 0.5
    u, d = (o2 / 2) ** 2, p
    up = 0.001 + (0.01 - 0.001) * u / (1 + u)
    expected = up + (0.0 - up) * d / (1 + d)  # PhysiCell 1.14.2: increases first, then decreases toward the minimum
    value = eval(expr.replace("^", "**"), {"max": max, "o2": o2, "p": p})
    assert value == pytest.approx(expected)


def test_contact_energies_follow_the_stated_mapping(tmp_path):
    model, _, root = exported("sorting", tmp_path)
    energy = {frozenset((c.get("type1"), c.get("type2"))): float(c.get("value"))
              for c in root.iterfind("CPM/Interaction/Contact")}  # fmt: skip
    assert energy[frozenset(("dark", "medium"))] == J_MEDIUM
    dark, light = model.cell_type("dark").mechanics, model.cell_type("light").mechanics
    cross = math.sqrt(dark.adhesion_strength * 0.1 * light.adhesion_strength * 0.1)
    assert energy[frozenset(("dark", "light"))] == pytest.approx(2 * J_MEDIUM - J_MEDIUM * cross / ADHESION_REF)
    assert energy[frozenset(("dark",))] < energy[frozenset(("light",))] < energy[frozenset(("dark", "light"))]


def test_fields_pulses_and_attacks(tmp_path):
    _, report, root = exported("chemotaxis", tmp_path, node_length=4.0, time_minutes=0.5)
    eqn = root.find("Global/System/DiffEqn[@symbol-ref='attractant']")
    assert float(eqn.findtext("Diffusion")) == pytest.approx(1000 * 0.5 / 16)  # micron^2/min to nodes^2/step
    sides = {b.get("boundary"): b.get("value") for b in root.iterfind("Global/Field/BoundaryValue")}
    assert sides == {"x": "1.0"}  # the other edges are no-flux, as the lattice's are
    assert {c.get("type") for c in root.iterfind("Space/Lattice/BoundaryConditions/Condition")} == {"noflux"}
    assert root.find("CellTypes/CellType/Chemotaxis").get("field") == "attractant"
    _, report, root = exported("drug_pulse", tmp_path)
    conditions = [e.findtext("Condition") for e in root.iterfind("Global/Event")]
    assert conditions == ["time >= 360.0", "time >= 720.0"]
    assert status(report)["substrates[drug].boundary"] == "approximated"
    _, report, root = exported("immune", tmp_path)
    tumour = root.find("CellTypes/CellType[@name='tumour']")
    p = 0.02 * 30.0
    assert f"{1.0 * p / (1 + p)!r}" in tumour.findtext("System/DiffEqn[@symbol-ref='damage']/Reaction")
    assert status(report)["cell_types[tumour].damage"] == "approximated"


def test_renamed_types_and_3d_models_are_reported(tmp_path):
    model = load_model(ROOT / "examples" / "ir" / "monolayer.yaml")
    model.cell_types[0].name = "epithelial cell"
    model.rules = [r.model_copy(update={"cell_type": "epithelial cell"}) for r in model.rules]
    for p in model.initial_conditions.placements:
        p.cell_type = "epithelial cell"
    rep = export_model(model, tmp_path / "renamed")
    assert status(rep)["cell_types[*].name"] == "approximated"
    assert MorpheusModel.read(tmp_path / "renamed" / "model.xml").biological_types() == ["epithelial_cell"]
    assert symbol("3T3") == "s_3T3" and symbol("tumour cell") == "tumour_cell"
    engine = MorpheusEngine()
    model.space.dimensions = 3
    assert any("2D" in r for r in engine.refuse(model, Knobs()))


def test_import_reads_the_published_examples():
    sorting, report = import_model(FIX / "CellSorting_2D.xml")
    assert [c.name for c in sorting.cell_types] == ["ct1", "ct2"] and sorting.space.boundary == "periodic"
    disc = sorting.initial_conditions.placements[0]
    assert (disc.kind, disc.count, disc.centre, disc.radius) == ("disc", 50, (100.0, 100.0), pytest.approx(200 / 3))
    assert status(report)["cell_types[ct1].NeighborhoodReporter"] == "dropped"
    chemo, report = import_model(FIX / "AutocrineChemotaxis.xml", node_length=2.0, time_minutes=0.5)
    assert chemo.initial_conditions.placements[0].count == 120  # cell_density * size.x * size.y, evaluated
    assert chemo.substrates[0].name == "chemoattractant"  # the field's name; its symbol is U
    assert status(report)["substrates[chemoattractant].reactions"] == "dropped"
    assert _arith("size.x/2 + 1", {"size.x": 10.0}) == 6.0
    with pytest.raises(ValueError, match="not plain arithmetic"):
        _arith("rand_uni(0, 1)", {})


def test_round_trip_keeps_rates_volumes_and_adhesion_order(tmp_path):
    model = load_model(ROOT / "examples" / "ir" / "immune.yaml")
    export_model(model, tmp_path / "m", node_length=2.0)
    back, _ = import_model(tmp_path / "m" / "model.xml", node_length=2.0)
    assert [c.name for c in back.cell_types] == ["tumour", "effector"]
    for ct in model.cell_types:
        b = back.cell_type(ct.name)
        assert b.volume == pytest.approx(ct.volume, rel=0.05)
        assert b.cycle.rates == pytest.approx(ct.cycle.rates or [0.0])
        assert b.death.apoptosis_rate == pytest.approx(ct.death.apoptosis_rate)
        assert b.motility.speed == pytest.approx(ct.motility.speed)
    sorting = load_model(ROOT / "examples" / "ir" / "sorting.yaml")
    export_model(sorting, tmp_path / "s")
    back, _ = import_model(tmp_path / "s" / "model.xml", node_length=2.0)
    assert back.cell_type("dark").mechanics.adhesion_strength > back.cell_type("light").mechanics.adhesion_strength


def test_engine_translates_ir_models_once_per_model_and_translator(tmp_path, monkeypatch):
    engine = MorpheusEngine(cache_dir=tmp_path / "cache")
    monkeypatch.setattr(engine, "_route", lambda: ("native", "fake", None))
    monkeypatch.setenv("MORPHEUS_BIN", "/fake/morpheus")
    model = load_model(ROOT / "examples" / "ir" / "spheroid.yaml")
    p1 = engine.prepare(model, {"experiment.max_time": 60}, Knobs(), 1, workdir=tmp_path / "w")
    engine.prepare(model, {"experiment.max_time": 60}, Knobs(), 2, workdir=tmp_path / "w")
    assert len(list((tmp_path / "cache" / "exports").iterdir())) == 1
    assert p1.context["ir_type_names"] == ["tumour"] and p1.context["scales"] == (2.0, 1.0)
    assert p1.context["field_names"] == {"oxygen": "oxygen"} and p1.context["origin"][0] == -400
    logger = MorpheusModel.read(p1.workdir / "model.xml").root.find("Analysis/Logger[@name='abmfact cells']")
    assert [s.get("symbol-ref") for s in logger.iterfind("Input/Symbol")][-1] == "state"
    monkeypatch.setattr("abmfact.engines.morpheus.translate.translator_fingerprint", lambda: "f" * 64)
    engine.prepare(model, {"experiment.max_time": 60}, Knobs(), 1, workdir=tmp_path / "w")
    assert len(list((tmp_path / "cache" / "exports").iterdir())) == 2


def test_recommend_shows_the_morpheus_translation():
    rec = {r.engine: r for r in recommend(ROOT / "examples" / "ir" / "monolayer.yaml",
                                          {"morpheus": MorpheusEngine()})}["morpheus"]  # fmt: skip
    assert rec.runs and rec.route == "translated" and rec.tier == "T1" and rec.fidelity["approximated"] > 0


@pytest.mark.morpheus
def test_an_exported_immune_model_runs_and_kills_tumour_cells(tmp_path):
    engine = MorpheusEngine(cache_dir=tmp_path / "cache")
    if not engine.availability().ok:
        pytest.skip(f"Morpheus cannot run here: {engine.availability().detail}")
    rec = engine.run_once(load_model(ROOT / "examples" / "ir" / "immune.yaml"), {"experiment.max_time": 720}, 0,
                          Knobs(), workdir=tmp_path / "w")  # fmt: skip
    assert rec.status == "ok", rec.error
    live = rec.outputs.scalars["tumour_live"]
    assert live[0] == 100 and live[-1] < live[0] and rec.outputs.type_names == ["tumour", "effector"]
