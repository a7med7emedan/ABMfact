"""Translation between ABM-IR and CompuCell3D projects; only the tests marked ``compucell3d`` run CompuCell3D."""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from abmfact.engines.compucell3d import CC3DProject, CompuCell3DEngine
from abmfact.engines.compucell3d.translate import (
    ADHESION_REF,
    J_MEDIUM,
    MAX_DIFFUSION,
    cc3d_name,
    export_project,
    import_project,
)
from abmfact.engines.recommend import recommend
from abmfact.ir import apply_params, load_model
from abmfact.knobs import Knobs

ROOT = Path(__file__).parent.parent
FIX = ROOT / "tests" / "fixtures" / "compucell3d"


def exported(name: str, tmp_path: Path, **kw):
    model = load_model(ROOT / "examples" / "ir" / f"{name}.yaml")
    report = export_project(model, tmp_path / name, **kw)
    sim = tmp_path / name / "Simulation"
    return (
        model,
        report,
        ET.parse(sim / "abmfact_model.xml").getroot(),
        json.loads((sim / "abmfact_model.json").read_text()),
    )


def status(report) -> dict[str, str]:
    return {e.path: e.status for e in report.entries}


def test_export_writes_a_project_compucell3d_can_read(tmp_path):
    _, report, root, spec = exported("monolayer", tmp_path)
    project = CC3DProject.read(tmp_path / "monolayer")
    assert project.cell_types() == ["epithelial"] and project.steps() == 2880
    assert project.dimensions() == (250, 250, 1)  # 500 micron at 2 micron per site
    assert root.findtext("Potts/Boundary_x") == "Periodic"
    assert [p.name for p in project.python_files()] == ["abmfact_main.py", "abmfact_ir_steppables.py"]
    assert spec["phenotype_every"] == 6 and spec["site_length"] == 2.0
    t = spec["types"][0]
    r = (3 * 2494 / (4 * math.pi)) ** (1 / 3)
    assert t["target_volume"] == round(math.pi * r * r / 4)  # the cell's cross-section in 4 square micron sites
    assert t["base"]["cycle entry"] == pytest.approx(0.00139)  # per minute and per MCS alike at 1 minute per MCS
    assert t["rules"]["cycle entry"][0]["signal"] == "pressure"
    assert status(report)["rules.pressure"] == "approximated" and status(report)["units"] == "approximated"


def test_contact_energies_follow_the_stated_mapping(tmp_path):
    model, _, root, _ = exported("sorting", tmp_path)
    energy = {(e.get("Type1"), e.get("Type2")): float(e.text) for e in root.iterfind("Plugin[@Name='Contact']/Energy")}
    assert energy[("Medium", "dark")] == J_MEDIUM
    dark, light = model.cell_type("dark").mechanics, model.cell_type("light").mechanics
    e = math.sqrt(dark.adhesion_strength * 1.0 * dark.adhesion_strength * 1.0)
    assert energy[("dark", "dark")] == pytest.approx(max(2.0, 2 * J_MEDIUM - J_MEDIUM * e / ADHESION_REF))
    cross = math.sqrt(dark.adhesion_strength * 0.1 * light.adhesion_strength * 0.1)
    assert energy[("dark", "light")] == pytest.approx(2 * J_MEDIUM - J_MEDIUM * cross / ADHESION_REF)
    assert energy[("dark", "dark")] < energy[("light", "light")] < energy[("dark", "light")]


def test_diffusion_is_converted_and_capped_with_a_note(tmp_path):
    _, report, root, spec = exported("chemotaxis", tmp_path, site_length=4.0, mcs_minutes=0.5)
    d = float(root.findtext(".//DiffusionField[@Name='attractant']/DiffusionData/GlobalDiffusionConstant"))
    assert d == pytest.approx(1000 * 0.5 / 16)  # micron^2/min to sites^2/MCS
    assert root.find("Plugin[@Name='Secretion']") is not None
    assert spec["types"][0]["base"]["migration speed"] == pytest.approx(2 * 0.5 / 4)
    assert spec["types"][0]["chemotaxis"] == {"field": "attractant", "direction": 1}
    plane = root.find(".//DiffusionField[@Name='attractant']/BoundaryConditions/Plane[@Axis='X']")
    assert plane.find("ConstantValue[@PlanePosition='Max']").get("Value") == "1.0"
    assert plane.find("ConstantDerivative[@PlanePosition='Min']") is not None
    _, report, root, _ = exported("spheroid", tmp_path)
    capped = float(root.findtext(".//DiffusionField[@Name='oxygen']/DiffusionData/GlobalDiffusionConstant"))
    assert capped == MAX_DIFFUSION
    note = {e.path: e.note for e in report.entries}["substrates[oxygen].diffusion_coefficient"]
    assert "capped" in note and "site_length" in note


def test_events_attacks_and_names(tmp_path):
    _, report, _, spec = exported("drug_pulse", tmp_path)
    assert spec["events"] == [{"time": 360.0, "until": 720.0, "action": "set_substrate_boundary", "target": "drug",
                               "value": 1.0}]  # fmt: skip
    assert status(report)["events[0]"] == "approximated"
    _, report, _, spec = exported("immune", tmp_path)
    eff = next(t for t in spec["types"] if t["name"] == "effector")
    assert eff["attack_rates"] == {"tumour": 0.02} and eff["attack_duration"] == 30.0 and eff["damage_rate"] == 1.0
    assert status(report)["cell_types[effector].interactions"] == "exact"
    assert cc3d_name("tumor cell") == "tumor_cell" and cc3d_name("3T3") == "t_3T3"
    renamed = apply_params(load_model(ROOT / "examples" / "ir" / "monolayer.yaml"), {})
    renamed.cell_types[0].name = "epithelial cell"
    rep = export_project(renamed, tmp_path / "renamed")
    assert status(rep)["cell_types[*].name"] == "approximated"
    assert CC3DProject.read(tmp_path / "renamed").cell_types() == ["epithelial_cell"]


def test_import_reads_what_the_cc3dml_declares():
    model, report = import_project(FIX / "cellsorting")
    assert [c.name for c in model.cell_types] == ["Condensing", "NonCondensing"]
    cond, non = model.cell_type("Condensing").mechanics, model.cell_type("NonCondensing").mechanics
    assert cond.adhesion_strength > non.adhesion_strength  # J = 2 binds more than J = 16 against medium 16
    assert [p.kind for p in model.initial_conditions.placements] == ["disc", "disc"]
    assert model.experiment.max_time == 10000 and model.native_extensions[0].engine == "compucell3d"
    assert status(report)["native_extensions[python]"] == "native"
    diffusion, report = import_project(FIX / "diffusion", site_length=2.0, mcs_minutes=0.5)
    fgf = diffusion.substrate("FGF")
    assert fgf.diffusion_coefficient == pytest.approx(0.010 * 4 / 0.5)
    assert status(report)["substrates[FGF].initial_value"] == "dropped"  # read from a file in the demo


def test_round_trip_keeps_types_volumes_and_adhesion_order(tmp_path):
    model = load_model(ROOT / "examples" / "ir" / "sorting.yaml")
    export_project(model, tmp_path / "p", site_length=2.0)
    back, _ = import_project(tmp_path / "p", site_length=2.0)
    assert [c.name for c in back.cell_types] == ["dark", "light"]
    for ct in model.cell_types:
        assert back.cell_type(ct.name).volume == pytest.approx(ct.volume, rel=0.05)
    assert back.cell_type("dark").mechanics.adhesion_strength > back.cell_type("light").mechanics.adhesion_strength


def test_engine_exports_ir_models_once_per_model_and_translator(tmp_path, monkeypatch):
    engine = CompuCell3DEngine(cache_dir=tmp_path / "cache")
    monkeypatch.setattr(engine, "_route", lambda: ("native", "fake", None))
    monkeypatch.setenv("CC3D_PYTHON", "/fake/python")
    model = load_model(ROOT / "examples" / "ir" / "immune.yaml")
    p1 = engine.prepare(model, {"experiment.max_time": 60}, Knobs(), 1, workdir=tmp_path / "w")
    engine.prepare(model, {"experiment.max_time": 60}, Knobs(), 2, workdir=tmp_path / "w")
    assert len(list((tmp_path / "cache" / "exports").iterdir())) == 1
    assert [o["name"] for o in p1.context["observables"]] == ["tumour_live", "tumour_dead", "effectors"]
    assert p1.context["ir_type_names"] == ["tumour", "effector"] and p1.context["scales"] == (2.0, 1.0)
    assert p1.argv[-1] == "60"  # saves every hour of model time
    monkeypatch.setattr("abmfact.engines.compucell3d.translate.exporter_fingerprint", lambda: "f" * 64)
    engine.prepare(model, {"experiment.max_time": 60}, Knobs(), 1, workdir=tmp_path / "w")
    assert len(list((tmp_path / "cache" / "exports").iterdir())) == 2


def test_recommend_shows_the_compucell3d_translation():
    rec = {r.engine: r for r in recommend(ROOT / "examples" / "ir" / "monolayer.yaml",
                                          {"compucell3d": CompuCell3DEngine()})}["compucell3d"]  # fmt: skip
    assert rec.runs and rec.route == "translated" and rec.tier == "T1" and rec.fidelity["approximated"] > 0
    assert any(path == "rules.pressure" for path, _, _ in rec.losses)


@pytest.mark.compucell3d
def test_an_exported_monolayer_runs_with_every_cell_placed(tmp_path):
    engine = CompuCell3DEngine(cache_dir=tmp_path / "cache")
    if not engine.availability().ok:
        pytest.skip(f"CompuCell3D cannot run here: {engine.availability().detail}")
    rec = engine.run_once(load_model(ROOT / "examples" / "ir" / "monolayer.yaml"), {"experiment.max_time": 60}, 0,
                          Knobs(), workdir=tmp_path / "w")  # fmt: skip
    assert rec.status == "ok", rec.error
    assert rec.outputs.scalars["cells"][0] == 40 and rec.outputs.type_names == ["epithelial"]
