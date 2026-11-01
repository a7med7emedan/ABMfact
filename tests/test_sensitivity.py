"""Sensitivity mode: Sobol and Morris indices through SALib, and the warnings about small designs."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from abmfact.cli import main
from abmfact.modes.design import DesignError, Parameter
from abmfact.modes.sensitivity import (
    SensitivitySettings,
    analyse,
    design_warnings,
    load_sensitivity,
    noise_share,
    result_warnings,
    sample,
)
from abmfact.modes.sweep import SweepResult

pytest.importorskip("SALib")

ROOT = Path(__file__).parent.parent
MODEL = ROOT / "examples" / "ir" / "monolayer.yaml"
ISHIGAMI = [Parameter(f"x{i}", -np.pi, np.pi) for i in (1, 2, 3)]
# Analytical indices of the Ishigami function with a = 7, b = 0.1 (Saltelli et al., 2008).
S1 = [0.3139, 0.4424, 0.0]
ST = [0.5576, 0.4424, 0.2437]


def ishigami(x: np.ndarray) -> np.ndarray:
    return np.sin(x[:, 0]) + 7 * np.sin(x[:, 1]) ** 2 + 0.1 * x[:, 2] ** 4 * np.sin(x[:, 0])


def scaled(unit: np.ndarray) -> np.ndarray:
    return np.stack([p.scaled(unit[:, i]) for i, p in enumerate(ISHIGAMI)], axis=1)


def test_sobol_indices_of_the_ishigami_function_fall_within_their_intervals():
    s = SensitivitySettings(method="sobol", n=4096, seed=2)
    unit = sample(ISHIGAMI, s)
    assert unit.shape == (4096 * 5, 3)
    out = analyse(ISHIGAMI, unit, ishigami(scaled(unit)), s)["indices"]
    for i, p in enumerate(ISHIGAMI):
        v = out[p.name]
        assert abs(v["S1"] - S1[i]) <= max(v["S1_conf"], 0.02)
        assert abs(v["ST"] - ST[i]) <= max(v["ST_conf"], 0.02)
    assert design_warnings(ISHIGAMI, s) == []
    assert result_warnings("sobol", {"indices": out}, None) == []


def test_morris_ranks_the_ishigami_inputs():
    s = SensitivitySettings(method="morris", n=40, levels=4, seed=1)
    unit = sample(ISHIGAMI, s)
    out = analyse(ISHIGAMI, unit, ishigami(scaled(unit)), s)["indices"]
    assert out["x3"]["mu_star"] < max(out["x1"]["mu_star"], out["x2"]["mu_star"])
    assert all(v["mu_star_conf"] >= 0 for v in out.values())


def test_small_or_odd_designs_are_flagged_before_any_run():
    w = design_warnings(ISHIGAMI, SensitivitySettings(method="sobol", n=100, second_order=True))
    assert any("power of two" in x for x in w) and any("below 512" in x for x in w)
    assert any("second-order" in x for x in w)
    w = design_warnings(ISHIGAMI, SensitivitySettings(method="morris", n=4, levels=3))
    assert any("trajectories" in x for x in w) and any("even number" in x for x in w)


def test_wide_intervals_and_seed_noise_are_flagged_after_the_runs():
    wide = {"indices": {"a": {"S1": 0.5, "S1_conf": 0.3, "ST": 0.6, "ST_conf": 0.05}}}
    w = result_warnings("sobol", wide, 0.5)
    assert any("half-widths above" in x for x in w) and any("seed noise" in x for x in w)


def test_noise_share_compares_seed_variance_with_the_total():
    runs = [{"point": p, "metrics": {"m": v}} for p, vals in (("a", (1.0, 1.2)), ("b", (5.0, 5.2))) for v in vals]
    share = noise_share(SweepResult({"runs": runs}), "m")
    assert 0 < share < 0.01
    assert noise_share(SweepResult({"runs": runs[::2]}), "m") is None


def test_missing_outputs_stop_the_analysis():
    s = SensitivitySettings(method="sobol", n=8)
    unit = sample(ISHIGAMI, s)
    y = ishigami(scaled(unit))
    y[3] = np.nan
    with pytest.raises(ValueError, match="no value"):
        analyse(ISHIGAMI, unit, y, s)


def sens_file(tmp_path: Path, **extra) -> Path:
    raw = {
        "model": str(MODEL),
        "params": {"experiment.max_time": 60, "experiment.save_interval": 60},
        "parameters": [{"name": "rules[0].half_max", "low": 0.25, "high": 1.0},
                       {"name": "cell_types[epithelial].cycle.rates[0]", "low": 0.0007, "high": 0.0028,
                        "scale": "log"}],
        "sensitivity": {"method": "morris", "n": 2, "levels": 4},
        "metrics": ["last(cells)"],
        "jobs": 2,
        **extra,
    }  # fmt: skip
    path = tmp_path / "sens.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_files_without_a_sensitivity_section_or_with_a_design_are_refused(tmp_path):
    with pytest.raises(DesignError, match="draws its own points"):
        load_sensitivity(sens_file(tmp_path, design={"method": "lhs", "n": 4}))
    with pytest.raises(DesignError, match="unknown sensitivity keys"):
        load_sensitivity(sens_file(tmp_path, sensitivity={"method": "sobol", "colour": 1}))
    spec, s = load_sensitivity(sens_file(tmp_path), {"method": "sobol", "n": 4})
    assert (s.method, s.n, spec.method) == ("sobol", 4, "sobol")


def test_the_command_runs_morris_end_to_end_and_warns(tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["sensitivity", str(sens_file(tmp_path)), "--out", str(out)]) == 0
    d = json.loads((out / "indices.json").read_text())
    assert d["sensitivity"]["points"] == 2 * 3 and d["counts"]["failed"] == 0
    assert set(d["metrics"]["last(cells)"]["indices"]) == {"rules[0].half_max", "cell_types[epithelial].cycle.rates[0]"}
    assert any("trajectories" in w for w in d["warnings"])
    assert (out / "sweep" / "runs.csv").exists() and "Warnings" in (out / "report.html").read_text()
    assert "warning:" in capsys.readouterr().out
