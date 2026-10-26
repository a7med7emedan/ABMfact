"""Translation between PhysiCell projects and ABM-IR, both ways, with fidelity reports."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from abmfact import cli
from abmfact.engines.physicell import PhysiCellEngine, SettingsXML, read_rules_csv
from abmfact.engines.physicell.build import Toolchain
from abmfact.engines.physicell.translate import TEMPLATE, export_project, import_project
from abmfact.ir import load_model, model_to_dict
from abmfact.knobs import Knobs

FIX = Path(__file__).parent / "fixtures" / "physicell"


@pytest.fixture
def rules_sample(tmp_path) -> Path:
    """The rules_sample configuration on the template's C++, as a project folder."""
    proj = tmp_path / "rules_sample"
    shutil.copytree(FIX / "template", proj, ignore=shutil.ignore_patterns("config"))
    shutil.copytree(FIX / "rules_sample" / "config", proj / "config")
    return proj


def test_import_rules_sample(rules_sample, tmp_path):
    model, rep = import_project(rules_sample, out_dir=tmp_path / "ir")
    assert [c.name for c in model.cell_types][:2] == ["malignant epithelial cell", "M0 macrophage"]
    assert len(model.rules) == 27 and model.rules[0].behaviour == "necrosis"
    oxygen = model.substrate("oxygen")
    assert (
        oxygen.boundary.kind == "dirichlet" and oxygen.boundary.value == 38.0 and oxygen.diffusion_coefficient == 100000
    )
    tumour = model.cell_type("malignant epithelial cell")
    assert tumour.cycle.model == "flow_cytometry_separated" and len(tumour.cycle.rates) == 4
    assert model.cell_type("M0 macrophage").cycle.model == "live"
    assert model.initial_conditions.placements[0].kind == "positions_csv"
    assert (tmp_path / "ir" / model.initial_conditions.placements[0].path).exists()
    assert model.native_extensions[0].kind == "custom_cpp" and "template" in model.native_extensions[0].description
    assert model.experiment.max_time == 7200 and model.space.dimensions == 2
    counts = rep.counts()
    assert counts["native"] == 1 and counts["exact"] > 20 and rep.tier == "T1"
    paths = {e.path: e for e in rep.entries}
    assert paths["initial_conditions.cells_csv"].status == "approximated"
    assert paths["cell_types[malignant epithelial cell].motility.advanced_chemotaxis"].status == "dropped"


def test_round_trip_keeps_the_model(rules_sample, tmp_path):
    model, _ = import_project(rules_sample, out_dir=tmp_path / "ir")
    rep = export_project(model, tmp_path / "out")
    assert (tmp_path / "out" / "main.cpp").exists() and (tmp_path / "out" / "custom_modules" / "custom.cpp").exists()
    rows, _ = read_rules_csv(tmp_path / "out" / "config" / "cell_rules.csv")
    assert len(rows) == 27
    again, _ = import_project(tmp_path / "out")
    a, b = model_to_dict(model, full=True), model_to_dict(again, full=True)
    for key in ("space", "substrates", "cell_types", "rules", "experiment", "observables"):
        assert a[key] == b[key], key
    original = (tmp_path / "ir" / model.initial_conditions.placements[0].path).read_text().splitlines()[1:]
    exported = (tmp_path / "out" / "config" / "cells.csv").read_text().splitlines()[1:]
    assert [r.split(",")[:2] for r in exported] == [[str(float(v)) for v in r.split(",")[:2]] for r in original]
    assert rep.counts()["dropped"] == 0


def test_export_of_the_ir_examples(tmp_path):
    for name in ("monolayer", "spheroid", "chemotaxis"):
        model = load_model(f"examples/ir/{name}.yaml")
        rep = export_project(model, tmp_path / name)
        s = SettingsXML.read(tmp_path / name / "config" / "PhysiCell_settings.xml")
        assert [c.get("name") for c in s.cell_definitions()] == [c.name for c in model.cell_types]
        assert s.time_steps()["dt_diffusion"] <= s.time_steps()["dt_mechanics"]
        notes = {e.path: e for e in rep.entries}
        assert notes["initial_conditions.placements"].status == "approximated"
        if name == "monolayer":
            assert notes["space"].status == "approximated"  # periodic in ABM-IR, walled in PhysiCell
            assert notes["substrates"].status == "approximated"
        if name in ("monolayer", "chemotaxis"):
            assert "dt_diffusion" in notes["experiment.dt_diffusion"].note
    for name in ("monolayer", "spheroid", "chemotaxis"):
        s = SettingsXML.read(tmp_path / name / "config" / "PhysiCell_settings.xml")
        declared = {v.get("name") for v in s.root.iterfind("microenvironment_setup/variable")}
        named = {(el.text or "").strip() for el in s.root.iterfind(".//motility/options/chemotaxis/substrate")}
        named |= {el.get("name") for el in s.root.iterfind(".//secretion/substrate")}
        named |= {el.get("substrate") for el in s.root.iterfind(".//chemotactic_sensitivity")}
        # PhysiCell refuses a cell definition that names a substrate the microenvironment does not declare.
        assert named <= declared, (name, named - declared)
    chemo = SettingsXML.read(tmp_path / "chemotaxis" / "config" / "PhysiCell_settings.xml")
    side = chemo.find("microenvironment_setup/variable/Dirichlet_options/boundary_value[@ID='xmax']")
    assert side is not None and side.get("enabled") == "True" and side.text == "1.0"
    assert chemo.get("cell_definitions/cell_definition/phenotype/motility/options/chemotaxis/enabled") == "true"


def test_export_events_intracellular_and_custom_cycles(tmp_path):
    (tmp_path / "net.bnd").write_text("Node A {}")
    (tmp_path / "net.cfg").write_text("$time_scale = 1;")
    doc = {
        "name": "x",
        "space": {"bounds": {"x_min": -50, "x_max": 50, "y_min": -50, "y_max": 50}, "voxel": {"dx": 10, "dy": 10}},
        "substrates": [{"name": "drug", "diffusion_coefficient": 10}],
        "cell_types": [{"name": "a", "cycle": {"model": "custom", "rates": [0.01, 0.02]}},
                       {"name": "b", "cycle": {"model": "ki67_basic", "durations": [100, 200]}}],
        "intracellular": [{"cell_type": "a", "format": "maboss", "files": {"bnd": "net.bnd", "cfg": "net.cfg"},
                           "settings": {"intracellular_dt": 1}},
                          {"cell_type": "b", "format": "sbml", "files": {"sbml": "m.xml"}}],
        "initial_conditions": {"placements": [{"kind": "positions", "cell_type": "a", "positions": [[1, 2]]}]},
        "events": [{"time": 10, "action": "set_substrate", "target": "drug", "value": 1}],
        "native_extensions": [{"engine": "netlogo", "kind": "other"}],
        "experiment": {"max_time": 60},
    }  # fmt: skip
    (tmp_path / "m.json").write_text(json.dumps(doc))
    rep = export_project(load_model(tmp_path / "m.json"), tmp_path / "out")
    status = {e.path: e.status for e in rep.entries}
    assert status["cell_types[a].cycle"] == "approximated" and status["cell_types[b].cycle"] == "exact"
    assert status["events[0]"] == "dropped" and status["intracellular[b]"] == "dropped"
    assert status["intracellular[a]"] == "approximated" and status["native_extensions[netlogo]"] == "dropped"
    assert status["initial_conditions.placements"] == "exact"
    s = SettingsXML.read(tmp_path / "out" / "config" / "PhysiCell_settings.xml")
    assert s.find("cell_definitions/cell_definition/phenotype/intracellular[@type='maboss']") is not None
    assert (tmp_path / "out" / "config" / "net.bnd").exists()
    assert s.find("cell_definitions/cell_definition[@name='b']/phenotype/cycle").get("code") == "1"
    again, _ = import_project(tmp_path / "out", out_dir=tmp_path / "back")
    assert again.intracellular[0].format == "maboss" and (tmp_path / "back" / "net.bnd").exists()
    assert again.cell_type("b").cycle.durations == [100.0, 200.0]


def test_import_and_export_commands(rules_sample, tmp_path, capsys):
    out = tmp_path / "ir" / "model.yaml"
    out.parent.mkdir()
    assert cli.main(["import", "physicell", str(rules_sample), "-o", str(out)]) == 0
    text = capsys.readouterr().out
    assert "fidelity T1" in text and "native" in text and out.exists()
    assert json.loads(out.with_suffix(".fidelity.json").read_text())["counts"]["native"] == 1
    proj = tmp_path / "proj"
    assert cli.main(["export", str(out), "--to", "physicell", "-o", str(proj), "--base", str(TEMPLATE)]) == 0
    assert (proj / "config" / "PhysiCell_settings.xml").exists() and (proj / "fidelity.md").exists()
    assert cli.main(["import", "physicell", str(tmp_path / "nothing"), "-o", str(out), "-q"]) == 2
    (tmp_path / "badproj" / "config").mkdir(parents=True)
    (tmp_path / "badproj" / "config" / "PhysiCell_settings.xml").write_text("<x/>")
    assert cli.main(["import", "physicell", str(tmp_path / "badproj"), "-o", str(out), "-q"]) == 2


def test_engine_exports_ir_models_once_per_hash(tmp_path, monkeypatch):
    engine = PhysiCellEngine(cache_dir=tmp_path / "cache", threads=1)
    engine._toolchain = Toolchain("native", "fake", compiler="/fake/g++")
    monkeypatch.setattr("abmfact.engines.physicell.engine.build_project", lambda *a, **k: tmp_path / "build")
    model = load_model("examples/ir/chemotaxis.yaml")
    params = {"experiment.max_time": 60, "user_parameters.number_of_cells": 0}
    p1 = engine.prepare(model, params, Knobs(), 1, workdir=tmp_path / "w")
    p2 = engine.prepare("examples/ir/chemotaxis.yaml", params, Knobs(), 2, workdir=tmp_path / "w")
    assert len(list((tmp_path / "cache" / "exports").iterdir())) == 1
    s = SettingsXML.read(p1.workdir / "config" / "PhysiCell_settings.xml")
    assert s.get("overall/max_time") == "60.0" and s.get("options/random_seed") == "1"
    assert [o["name"] for o in p1.context["observables"]] == ["mean_x", "cells", "attractant_mean"]
    assert p2.request.seed == 2 and not p1.context.get("warnings")
    engine.prepare(model, {"experiment.max_time": 30}, Knobs(), 1)
    assert len(list((tmp_path / "cache" / "exports").iterdir())) == 2
    # A changed exporter never reuses an export written by the old one.
    monkeypatch.setattr("abmfact.engines.physicell.translate.exporter_fingerprint", lambda: "f" * 64)
    engine.prepare(model, {"experiment.max_time": 30}, Knobs(), 1)
    assert len(list((tmp_path / "cache" / "exports").iterdir())) == 3


def test_native_runs_warn_when_diffusion_steps_exceed_mechanics(tmp_path, monkeypatch):
    engine = PhysiCellEngine(cache_dir=tmp_path / "cache")
    engine._toolchain = Toolchain("native", "fake", compiler="/fake/g++")
    monkeypatch.setattr("abmfact.engines.physicell.engine.build_project", lambda *a, **k: tmp_path / "build")
    prep = engine.prepare(FIX / "template", {"overall/dt_diffusion": 1.0}, Knobs(), 1)
    assert "slowly" in prep.context["warnings"][0]


@pytest.mark.physicell
def test_ir_model_runs_in_physicell_like_in_the_reference_engine(tmp_path):
    from test_physicell_engine import _toolchain_or_skip

    from abmfact.reference import simulate

    _toolchain_or_skip()
    model = load_model("examples/ir/chemotaxis.yaml")
    engine = PhysiCellEngine(cache_dir=tmp_path / "cache", threads=1)
    rec = engine.run_once(model, {"experiment.max_time": 240}, 0, Knobs(), workdir=tmp_path / "w")
    assert rec.status == "ok", rec.error
    pc = rec.outputs.scalars["mean_x"]
    ref = simulate(load_model("examples/ir/chemotaxis.yaml"), 0).scalars["mean_x"][: len(pc)]
    # Both engines move the cells up the gradient at a similar speed (a T1 translation, not a docking test).
    assert pc[-1] - pc[0] > 200 and abs((pc[-1] - pc[0]) - (ref[-1] - ref[0])) < 0.3 * (ref[-1] - ref[0])


@pytest.mark.physicell
def test_ir_model_without_substrates_runs_in_physicell(tmp_path):
    from test_physicell_engine import _toolchain_or_skip

    _toolchain_or_skip()
    engine = PhysiCellEngine(cache_dir=tmp_path / "cache", threads=1)
    params = {"experiment.max_time": 60}
    rec = engine.run_once(load_model("examples/ir/monolayer.yaml"), params, 0, Knobs(), workdir=tmp_path / "w")
    assert rec.status == "ok", rec.error
    assert rec.outputs.scalars["cells"][0] == 40


def test_template_code_is_recognised_with_windows_line_endings(rules_sample):
    for f in (rules_sample / "custom_modules").iterdir():
        f.write_bytes(f.read_bytes().replace(b"\n", b"\r\n"))
    model, _ = import_project(rules_sample)
    assert "template" in model.native_extensions[0].description


def test_build_key_and_provenance_are_the_same_for_crlf_checkouts(rules_sample, tmp_path):
    from abmfact.engines.physicell.build import Toolchain, source_hash

    crlf = tmp_path / "crlf"
    shutil.copytree(rules_sample, crlf)
    for f in crlf.rglob("*"):
        if f.is_file() and (f.suffix in (".cpp", ".h", ".xml", ".csv") or f.name == "Makefile"):
            f.write_bytes(f.read_bytes().replace(b"\n", b"\r\n"))
    tc = Toolchain("native", "fake", compiler="/fake/g++")
    assert source_hash(crlf, tc, "id") == source_hash(rules_sample, tc, "id")
    a, _ = import_project(rules_sample)
    b, _ = import_project(crlf)
    assert a.provenance.derived_from.sha256 == b.provenance.derived_from.sha256
