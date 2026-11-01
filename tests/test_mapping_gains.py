"""The lattice mapping gains that T2 translation calibrates: pressure and motility scales."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abmfact.engines import get_engine
from abmfact.engines.base import MAPPING_GAINS, gain_suffix
from abmfact.engines.compucell3d.translate import export_project
from abmfact.engines.morpheus.translate import _num, export_model
from abmfact.ir import load_model
from abmfact.knobs import Knobs

ROOT = Path(__file__).parent.parent
MONOLAYER = load_model(ROOT / "examples" / "ir" / "monolayer.yaml")
CHEMOTAXIS = load_model(ROOT / "examples" / "ir" / "chemotaxis.yaml")


def test_gain_suffix_is_empty_at_one_so_t1_exports_keep_their_keys():
    assert MAPPING_GAINS == ("pressure_gain", "motility_gain")
    assert gain_suffix({"pressure_gain": 1.0, "motility_gain": 1.0}) == ""
    assert gain_suffix({"pressure_gain": 2.5, "motility_gain": 1.0}) == "-pressure_gain2.5"


def test_morpheus_scales_pressure_and_motility(tmp_path):
    rep1 = export_model(MONOLAYER, tmp_path / "a")
    rep2 = export_model(MONOLAYER, tmp_path / "b", pressure_gain=2.0)
    a, b = (tmp_path / "a" / "model.xml").read_text(), (tmp_path / "b" / "model.xml").read_text()
    assert f"{_num(2.0)} * contact / max(perimeter, 1)" in b and f"{_num(2.0)} * contact" not in a
    assert "times pressure_gain 2" in rep2.to_markdown() and "pressure_gain" not in rep1.to_markdown()
    export_model(CHEMOTAXIS, tmp_path / "c", motility_gain=0.5)
    c = (tmp_path / "c" / "model.xml").read_text()
    assert f"* {_num(25.0)} * speed * bias" in c and f"* {_num(50.0)} * speed" not in c


def test_compucell3d_scales_pressure_and_motility(tmp_path):
    export_project(MONOLAYER, tmp_path / "a", pressure_gain=3.0, motility_gain=2.0)
    spec = json.loads((tmp_path / "a" / "Simulation" / "abmfact_model.json").read_text())
    assert spec["pressure_gain"] == 3.0 and spec["motility_lambda"] == 100.0
    steppable = (tmp_path / "a" / "Simulation" / "abmfact_ir_steppables.py").read_text()
    assert 'SPEC["pressure_gain"] * contact_fraction' in steppable


@pytest.mark.parametrize("bad", [0, -1, float("nan")])
def test_gains_must_be_positive(tmp_path, bad):
    with pytest.raises(ValueError, match="positive"):
        export_model(MONOLAYER, tmp_path / "m", pressure_gain=bad)
    with pytest.raises(ValueError, match="positive"):
        export_project(MONOLAYER, tmp_path / "c", motility_gain=bad)


@pytest.mark.parametrize("name", ["morpheus", "compucell3d"])
def test_engine_options_reach_the_export_and_change_the_run_key(tmp_path, name):
    plain = get_engine(name, cache_dir=tmp_path / "cache")
    scaled = get_engine(name, cache_dir=tmp_path / "cache", pressure_gain=2.0)
    report = scaled.translation(str(ROOT / "examples" / "ir" / "monolayer.yaml"))
    assert any("pressure_gain 2" in e.note for e in report.entries)
    plain._export(MONOLAYER, {})
    scaled._export(MONOLAYER, {})
    exports = sorted(p.name for p in (tmp_path / "cache" / "exports").iterdir())
    assert len(exports) == 2 and sum(n.endswith("-pressure_gain2") for n in exports) == 1
    if not plain.availability().ok:
        pytest.skip(f"{name} cannot run here, so run keys are not built: {plain.availability().detail}")
    a = plain.prepare(str(ROOT / "examples" / "ir" / "monolayer.yaml"), {}, Knobs(), 0, tmp_path / "a")
    b = scaled.prepare(str(ROOT / "examples" / "ir" / "monolayer.yaml"), {}, Knobs(), 0, tmp_path / "b")
    assert a.request.key() != b.request.key()


def _cells(name: str, gain: float, tmp_path: Path) -> float:
    engine = get_engine(name, pressure_gain=gain)
    if not engine.availability().ok:
        pytest.skip(f"{name} cannot run here: {engine.availability().detail}")
    params = {"experiment.max_time": 1440, "experiment.save_interval": 720}
    rec = engine.run_once(str(ROOT / "examples" / "ir" / "monolayer.yaml"), params, 1, Knobs(), workdir=tmp_path)
    assert rec.status == "ok", rec.error
    return rec.outputs.scalars["cells"][-1]


@pytest.mark.morpheus
def test_a_lower_pressure_gain_lets_the_morpheus_monolayer_grow_larger(tmp_path):
    assert _cells("morpheus", 0.25, tmp_path / "lo") > _cells("morpheus", 4.0, tmp_path / "hi")


@pytest.mark.compucell3d
def test_a_lower_pressure_gain_lets_the_compucell3d_monolayer_grow_larger(tmp_path):
    assert _cells("compucell3d", 0.25, tmp_path / "lo") > _cells("compucell3d", 4.0, tmp_path / "hi")
