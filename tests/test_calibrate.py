"""Calibrate mode: targets, distances, Bayesian optimisation and the calibrated model or options."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
import yaml

from abmfact.cli import main
from abmfact.ir import load_model
from abmfact.modes.calibrate import (
    CalibrationError,
    CalibrationSpec,
    Problem,
    Target,
    bayesopt,
    collect,
    load_calibration,
    load_target_csv,
    spec_from_mapping,
    target_from_ensemble,
)
from abmfact.modes.design import Parameter

pytest.importorskip("bayes_opt")

ROOT = Path(__file__).parent.parent
MODEL = ROOT / "examples" / "ir" / "monolayer.yaml"
TIMES = np.array([0.0, 60.0, 120.0, 180.0])


class Growth(Problem):
    """An analytic stand-in for an engine: cells = 40 exp(rate t), with seed noise, and spread = 10 + 0.1 t."""

    def runs(self, params, options, label):
        rate = float(params.get("rate", options.get("rate", 0.0)))
        out = []
        for k in range(self.spec.seeds_per_evaluation):
            noise = np.random.default_rng(self.spec.seed_start + k).normal(0, 1, TIMES.size)
            out.append({"time": list(TIMES), "cells": list(40 * np.exp(rate * TIMES) + noise),
                        "spread": list(10 + 0.1 * TIMES)})  # fmt: skip
        return out, [f"{label}-{k}" for k in range(len(out))], []


def growth_target(rate: float, sd: float | None = 1.0) -> Target:
    cells = 40 * np.exp(rate * TIMES)
    return Target({"cells": (TIMES, cells, None if sd is None else np.full(TIMES.size, sd))}, {"kind": "test"})


def spec(param: Parameter, **kw) -> CalibrationSpec:
    return CalibrationSpec(title="t", model=MODEL, engine="reference", parameters=[param], target={"data": "x"},
                           seeds_per_evaluation=4, **kw)  # fmt: skip


def test_csv_targets_keep_their_spread_and_hash(tmp_path):
    path = tmp_path / "t.csv"
    path.write_text("time,observable,value,sd\n60,cells,50,2\n0,cells,40,0\n0,spread,10,\n60,spread,16,\n")
    t = load_target_csv(path)
    assert list(t.series["cells"][0]) == [0.0, 60.0] and t.series["spread"][2] is None
    labels, values, scales, kinds = t.features(["cells", "spread"])
    assert labels == [("cells", 0.0), ("cells", 60.0), ("spread", 0.0), ("spread", 60.0)]
    assert list(values) == [40.0, 50.0, 10.0, 16.0]
    assert list(scales) == [10.0, 2.0, 6.0, 6.0]  # cells at t=0 has sd 0: its range stands in
    assert kinds == {"cells": "sd, range where the sd is 0", "spread": "range"}
    assert len(t.source["sha256"]) == 64
    with pytest.raises(CalibrationError, match="no observable"):
        t.features(["dead"])
    (tmp_path / "bad.csv").write_text("t,v\n1,2\n")
    with pytest.raises(CalibrationError, match="needs columns"):
        load_target_csv(tmp_path / "bad.csv")


def test_ensemble_targets_use_each_save_times_mean_and_sd():
    data = {"series": {"cells": [{"time": 0, "mean": 40, "sd": 0}, {"time": 60, "mean": 55, "sd": 3}]},
            "ensemble": {"seeds": 5, "seed_start": 7}}  # fmt: skip
    t = target_from_ensemble(data, {"kind": "ensemble"})
    assert list(t.series["cells"][1]) == [40, 55] and t.source["seed_start"] == 7


def test_features_interpolate_between_saves_and_are_missing_outside():
    p = Growth(spec(Parameter("rate", 0.0, 0.01)), growth_target(0.005))
    f = p.features({"time": [0, 100, 200], "cells": [0, 10, 20]})
    assert list(f) == [0.0, 6.0, 12.0, 18.0]
    assert np.isnan(p.features({"time": [0, 20, 40, 60, 80, 100], "cells": [0, 2, 4, 6, 8, 10]})[2:]).all()
    # A last save one step short of a target time, as CompuCell3D's at 1439 of 1440 minutes, takes its value.
    assert p.features({"time": [0, 60, 119, 179], "cells": [1, 2, 3, 4]})[-1] == 4.0
    assert math.isinf(p.score(np.array([[np.nan] * 4])))


def test_bayesian_optimisation_recovers_a_planted_rate_with_an_interval_that_holds_it():
    p = Growth(spec(Parameter("rate", 0.001, 0.02, scale="log")), growth_target(0.006))
    out = bayesopt(p, {"n_init": 6, "n_iter": 14, "seed": 3})
    assert out["best"]["rate"] == pytest.approx(0.006, rel=0.05)
    lo, hi = out["interval"]["rate"]
    assert lo <= 0.006 <= hi
    assert out["diagnostics"]["search_evaluations"] == 20
    assert out["diagnostics"]["evaluations"] > 20  # the interval's edges were bisected with new evaluations
    assert out["diagnostics"]["best_so_far"][-1] == out["best_distance"]
    points = [e.values["rate"] for e in p.evaluations[:20]]
    assert len(set(points)) == len(points)  # repeats are replaced by fresh points


def test_bayesopt_settings_are_checked():
    p = Growth(spec(Parameter("rate", 0.001, 0.02)), growth_target(0.006))
    with pytest.raises(CalibrationError, match="unknown bayesopt settings"):
        bayesopt(p, {"budget": 3})


def test_a_best_value_at_the_edge_of_its_range_is_flagged():
    p = Growth(spec(Parameter("rate", 0.0005, 0.002)), growth_target(0.006))
    out = bayesopt(p, {"n_init": 4, "n_iter": 4, "seed": 0})
    assert any("edge of its range" in w for w in out["warnings"])


def test_results_write_the_calibrated_model_with_provenance_and_options(tmp_path):
    s = spec(Parameter("cell_types[epithelial].cycle.rates[0]", 0.001, 0.02, scale="log"),
             params={"experiment.max_time": 180})  # fmt: skip
    p = Growth(s, growth_target(0.006))
    p.runs = lambda params, options, label: Growth.runs(p, {"rate": params["cell_types[epithelial].cycle.rates[0]"]},
                                                         options, label)  # fmt: skip
    res = collect(s, p, bayesopt(p, {"n_init": 4, "n_iter": 4}), {"tier": "T0"})
    paths = res.write(tmp_path)
    m = load_model(paths["model"])
    assert m.cell_types[0].cycle.rates[0] == pytest.approx(res.best["cell_types[epithelial].cycle.rates[0]"])
    assert m.experiment.max_time == 2880  # fixed settings stay out of the calibrated model
    assert "Calibrated with ABMfact" in m.provenance.notes and "fixed settings" in m.provenance.notes
    assert "options" not in paths
    s2 = spec(Parameter("option:rate", 0.001, 0.02, scale="log"), options={"threads": 1})
    p2 = Growth(s2, growth_target(0.006))
    paths2 = collect(s2, p2, bayesopt(p2, {"n_init": 4, "n_iter": 2}), {}).write(tmp_path / "o")
    opts = json.loads(paths2["options"].read_text())
    assert set(opts["options"]) == {"threads", "rate"} and "model" not in paths2


def calibration_file(tmp_path: Path, **extra) -> Path:
    raw = {"model": str(MODEL), "engine": "reference",
           "params": {"experiment.max_time": 120, "experiment.save_interval": 60},
           "parameters": [{"name": "cell_types[epithelial].cycle.rates[0]", "low": 0.0007, "high": 0.0028}],
           "target": {"engine": "reference", "seeds": 2, "seed_start": 50}, "observables": ["cells"],
           "seeds_per_evaluation": 2, "bayesopt": {"n_init": 2, "n_iter": 1}, **extra}  # fmt: skip
    path = tmp_path / "cal.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("extra", "match"),
    [
        ({"colour": 1}, "unknown calibration keys"),
        ({"target": {"data": "a.csv", "engine": "reference"}}, "exactly one"),
        ({"target": {"engine": "reference", "count": 2}}, "unknown target keys"),
        ({"parameters": []}, "at least one free parameter"),
        ({"parameters": [{"name": "rules[9].half_max", "low": 0, "high": 1}]}, "does not fit the model"),
    ],
)
def test_bad_calibration_files_are_refused(tmp_path, extra, match):
    with pytest.raises(CalibrationError, match=match):
        load_calibration(calibration_file(tmp_path, **extra))


def test_the_command_calibrates_against_an_engine_ensemble(tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["calibrate", str(calibration_file(tmp_path)), "--out", str(out)]) == 0
    d = json.loads((out / "calibration.json").read_text())
    assert d["result"]["diagnostics"]["search_evaluations"] == 3 and d["target"]["kind"] == "engine"
    assert d["target_ensemble"]["runs"]["ok"] == 2
    assert {p.name for p in out.iterdir()} >= {"calibration.json", "evaluations.csv", "report.html", "calibrated.yaml"}
    assert "evaluation   3:" in capsys.readouterr().out
    assert main(["calibrate", str(calibration_file(tmp_path)), "--method", "nosuch", "--out", str(out)]) == 2


def test_spec_reads_the_methods_own_section(tmp_path):
    raw = {
        "model": str(MODEL),
        "engine": "reference",
        "parameters": [{"name": "rules[0].half_max", "low": 0.1, "high": 1}],
        "target": {"data": "x.csv"},
        "method": "abc-smc",
        "abc_smc": {"population": 50},
    }
    assert spec_from_mapping(raw, tmp_path).settings == {"population": 50}
