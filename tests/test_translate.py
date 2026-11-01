"""T2 translation: only mapping gains vary, held-out observables stay out of the fit, gains reach comparisons."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from abmfact.cli import main
from abmfact.modes import translate as tr
from abmfact.modes.calibrate import CalibrationError, CalibrationResult
from abmfact.modes.design import Parameter
from abmfact.records import FidelityReport
from abmfact.verify.compare import CompareError, load_comparison

ROOT = Path(__file__).parent.parent
MODEL = ROOT / "examples" / "ir" / "monolayer.yaml"
GAIN = Parameter("option:pressure_gain", 0.1, 10, scale="log")


def spec(**kw) -> tr.TranslationSpec:
    base = {
        "title": "t",
        "model": MODEL,
        "to": "morpheus",
        "calibrate_on": ["cells"],
        "hold_out": ["spread"],
        "free": [GAIN],
    }
    return tr.TranslationSpec(**{**base, **kw})


@pytest.mark.parametrize(
    ("kw", "match"),
    [
        ({"free": [Parameter("rules[0].half_max", 0.25, 1.0)]}, "only the mapping gains"),
        ({"free": [Parameter("option:temperature", 1, 20)]}, "only the mapping gains"),
        ({"free": [Parameter("option:pressure_gain", -1, 2)]}, "must be positive"),
        ({"free": []}, "at least one free mapping gain"),
        ({"hold_out": []}, "hold out at least one"),
        ({"hold_out": ["cells"]}, "both calibrated on and held out"),
        ({"calibrate_on": ["volume"]}, "no observables"),
        ({"to": "physicell"}, "targets"),
        ({"options": {"pressure_gain": 2.0}}, "both fixed options and free gains"),
    ],
)
def test_translations_that_could_match_by_construction_or_change_biology_are_refused(kw, match):
    with pytest.raises(CalibrationError, match=match):
        spec(**kw).check()


def test_gains_parse_from_the_command_line_on_a_log_scale():
    (p,) = tr.parse_free(["pressure_gain=0.2:5"])
    assert (p.name, p.low, p.high, p.scale) == ("option:pressure_gain", 0.2, 5.0, "log")
    with pytest.raises(CalibrationError, match="NAME=LOW:HIGH"):
        tr.parse_free(["pressure_gain"])


def test_translation_files_are_read_and_checked(tmp_path):
    raw = {
        "model": str(MODEL),
        "to": "morpheus",
        "calibrate_on": ["cells"],
        "hold_out": ["spread"],
        "free": [{"name": "pressure_gain", "low": 0.1, "high": 10}],
        "source": {"engine": "reference", "seeds": 5},
        "bayesopt": {"n_init": 3},
    }
    path = tmp_path / "t.yaml"
    path.write_text(yaml.safe_dump(raw))
    s = tr.load_translation(path)
    assert s.free[0].name == "option:pressure_gain" and s.free[0].scale == "log" and s.settings == {"n_init": 3}
    cal = s.calibration()
    assert cal.engine == "morpheus" and cal.target["engine"] == "reference" and cal.observables == ["cells"]
    path.write_text(yaml.safe_dump({**raw, "free": [{"name": "cell_types[epithelial].volume", "low": 1, "high": 2}]}))
    with pytest.raises(CalibrationError, match="only the mapping gains"):
        tr.load_translation(path)
    path.write_text(yaml.safe_dump({**raw, "source": {"engine": "reference", "count": 3}}))
    with pytest.raises(CalibrationError, match="unknown source keys"):
        tr.load_translation(path)


def fake_result(s: tr.TranslationSpec) -> tr.TranslationResult:
    cal = CalibrationResult(
        {
            "created": "2026-11-01T00:00:00+00:00",
            "result": {
                "best": {GAIN.name: 0.6},
                "interval": {GAIN.name: [0.5, 0.7]},
                "interval_meaning": "m",
                "best_distance": 0.1,
                "warnings": [],
            },
            "evaluations": [],
            "fit": {"observables": ["cells"]},
            "target": {"kind": "engine", "engine": "reference"},
            "calibration": {},
        },
        s.calibration(),
    )
    data = {
        "tier": "T2",
        "engine": s.to,
        "gains": {"pressure_gain": 0.6},
        "interval": {"pressure_gain": [0.5, 0.7]},
        "fixed_options": {},
        "calibrate_on": ["cells"],
        "source": {"engine": "reference", "seeds": 10},
        "held_out": [],
        "warnings": [],
    }
    return tr.TranslationResult(data, cal, s)


def test_the_written_translation_is_tier_t2_with_the_gain_in_the_project_and_report(tmp_path, monkeypatch):
    monkeypatch.setattr(CalibrationResult, "write", lambda self, out: {"json": Path(out) / "calibration.json"})
    paths = fake_result(spec()).write(tmp_path)
    assert "0.6 * contact / max(perimeter, 1)" in (tmp_path / "project" / "model.xml").read_text()
    fidelity = paths["fidelity"].read_text()
    assert "T2" in fidelity and "calibrated: pressure_gain 0.6 (0.5 to 0.7) on cells against reference" in fidelity
    d = json.loads(paths["json"].read_text())
    assert d["tier"] == "T2" and d["fidelity"]["tier"] == "T2"


def test_annotate_touches_only_the_entries_each_gain_governs():
    rep = FidelityReport("abm-ir", "morpheus:x")
    rep.add("rules.pressure", "approximated", "contact fraction")
    rep.add("cell_types[a].motility", "approximated", "persistent motion")
    tr.annotate(
        rep,
        {
            "gains": {"pressure_gain": 2.0},
            "interval": {"pressure_gain": [1.5, 2.5]},
            "calibrate_on": ["cells"],
            "source": {"engine": "reference", "seeds": 20},
        },
    )
    assert rep.tier == "T2"
    assert "calibrated: pressure_gain 2" in rep.entries[0].note and "calibrated" not in rep.entries[1].note


def test_comparisons_take_calibrated_gains_from_a_translation_file(tmp_path):
    (tmp_path / "translation.json").write_text(
        json.dumps(
            {
                "tier": "T2",
                "engine": "morpheus",
                "gains": {"pressure_gain": 0.6},
                "fixed_options": {"motility_gain": 1.3},
            }
        )
    )
    raw = {
        "model": str(MODEL),
        "engines": ["reference", "morpheus"],
        "metrics": [{"metric": "last(cells)", "budget": 1}],
        "options": {"morpheus": {"threads": 1}},
        "options_from": {"morpheus": "translation.json"},
    }
    (tmp_path / "c.yaml").write_text(yaml.safe_dump(raw))
    cmp = load_comparison(tmp_path / "c.yaml")
    assert cmp.options["morpheus"] == {"threads": 1, "motility_gain": 1.3, "pressure_gain": 0.6}
    (tmp_path / "second.json").write_text(
        json.dumps({"tier": "T2", "engine": "morpheus", "gains": {"motility_gain": 2.0}})
    )
    (tmp_path / "c.yaml").write_text(
        yaml.safe_dump({**raw, "options_from": {"morpheus": ["translation.json", "second.json"]}})
    )
    assert load_comparison(tmp_path / "c.yaml").options["morpheus"]["motility_gain"] == 2.0  # later files win
    (tmp_path / "c.yaml").write_text(yaml.safe_dump(raw))
    (tmp_path / "c.yaml").write_text(yaml.safe_dump({**raw, "options_from": {"compucell3d": "translation.json"}}))
    with pytest.raises(CompareError, match="not among the engines"):
        load_comparison(tmp_path / "c.yaml")
    (tmp_path / "translation.json").write_text(json.dumps({"tier": "T1", "engine": "morpheus", "gains": {}}))
    (tmp_path / "c.yaml").write_text(yaml.safe_dump(raw))
    with pytest.raises(CompareError, match="not a T2 translation"):
        load_comparison(tmp_path / "c.yaml")


def test_the_command_refuses_a_biological_free_parameter(tmp_path, capsys):
    code = main(
        [
            "translate",
            str(MODEL),
            "--to",
            "morpheus",
            "--calibrate-on",
            "cells",
            "--hold-out",
            "spread",
            "--free",
            "cell_types[epithelial].cycle.rates[0]=0.001:0.002",
            "--out",
            str(tmp_path),
        ]
    )
    assert code == 2 and "only the mapping gains" in capsys.readouterr().err
    assert main(["translate", str(MODEL), "--to", "morpheus", "--out", str(tmp_path)]) == 2


@pytest.mark.morpheus
def test_a_short_t2_translation_into_morpheus_runs_end_to_end(tmp_path):
    from abmfact.engines import get_engine

    if not get_engine("morpheus").availability().ok:
        pytest.skip("Morpheus cannot run here")
    s = spec(
        params={"experiment.max_time": 240, "experiment.save_interval": 120},
        source={"engine": "reference", "seeds": 3},
        seeds_per_evaluation=2,
        settings={"n_init": 2, "n_iter": 1, "refine": 1},
        jobs=4,
    )
    res = tr.translate(s, store=tmp_path / "store")
    assert res.data["tier"] == "T2" and 0.1 <= res.gains["pressure_gain"] <= 10
    assert res.data["held_out"][0]["metric"] == "last(spread)" and "difference" in res.data["held_out"][0]
    res.write(tmp_path / "out")
    assert (tmp_path / "out" / "calibration" / "calibration.json").exists()
