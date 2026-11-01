"""ABC-SMC through pyABC, and the calibration plug-in slots."""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from abmfact.modes import plugins
from abmfact.modes.abc_smc import abc_smc, weighted_quantile
from abmfact.modes.calibrate import CalibrationError, calibrators
from abmfact.modes.design import Parameter

pyabc = pytest.importorskip("pyabc")

from test_calibrate import Growth, growth_target, spec  # noqa: E402


def test_weighted_quantiles_follow_the_weights():
    x = np.array([1.0, 2.0, 3.0, 4.0])
    assert weighted_quantile(x, np.ones(4), 0.5) == 2.0
    assert weighted_quantile(x, np.array([0, 0, 0, 1.0]), 0.1) == 4.0


def test_abc_smc_puts_the_planted_rate_inside_its_credible_interval():
    p = Growth(spec(Parameter("rate", 0.001, 0.02, scale="log")), growth_target(0.006, sd=3.0))
    out = abc_smc(p, {"population": 40, "generations": 4, "max_simulations": 600, "seed": 1})
    lo, hi = out["interval"]["rate"]
    assert lo <= 0.006 <= hi and hi - lo < 0.01
    assert out["best"]["rate"] == pytest.approx(0.006, rel=0.25)
    gens = out["diagnostics"]["generations"]
    assert len(gens) >= 2 and gens[-1]["epsilon"] < gens[0]["epsilon"]
    assert all(g["ess"] > 0 for g in gens)
    assert len(out["posterior"]["rate"]["values"]) == 40
    assert "credible interval" in out["interval_meaning"]


def test_abc_smc_settings_are_checked():
    p = Growth(spec(Parameter("rate", 0.001, 0.02)), growth_target(0.006))
    with pytest.raises(CalibrationError, match="unknown abc_smc settings"):
        abc_smc(p, {"particles": 10})
    with pytest.raises(CalibrationError, match="population of 5"):
        abc_smc(p, {"population": 2})


def test_every_method_is_listed_with_the_plug_in_slots():
    assert {"bayesopt", "abc-smc", "uq-physicell", "fitmulticell"} <= set(calibrators())
    assert [s["method"] for s in plugins.status()] == ["uq-physicell", "fitmulticell"]


def test_uq_physicell_runs_abmfacts_abc_smc_with_its_safeguards(monkeypatch):
    made = {}

    class Checked(pyabc.ABCSMC):
        def __init__(self, *args, max_zero_weight_fraction=0.05, **kwargs):
            made["fraction"] = max_zero_weight_fraction
            super().__init__(*args, **kwargs)

    safeguards = types.ModuleType("uq_physicell.abc.safeguards")
    safeguards.CheckedABCSMC = Checked
    transitions = types.ModuleType("uq_physicell.abc.transitions")
    transitions.ScaledMultivariateNormalTransition = pyabc.MultivariateNormalTransition
    for name, mod in (
        ("uq_physicell", types.ModuleType("uq_physicell")),
        ("uq_physicell.abc", types.ModuleType("uq_physicell.abc")),
        ("uq_physicell.abc.safeguards", safeguards),
        ("uq_physicell.abc.transitions", transitions),
    ):
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setattr(plugins, "installed", lambda dist: "9.9")
    p = Growth(spec(Parameter("rate", 0.001, 0.02)), growth_target(0.006, sd=3.0))
    out = plugins.uq_physicell(
        p, {"population": 10, "generations": 2, "max_simulations": 100, "max_zero_weight_fraction": 0.2}
    )
    assert made["fraction"] == 0.2
    assert "UQ-PhysiCell 9.9" in out["diagnostics"]["method"]


def test_plug_ins_that_are_missing_or_not_bridged_say_so(monkeypatch):
    p = Growth(spec(Parameter("rate", 0.001, 0.02)), growth_target(0.006))
    monkeypatch.setattr(plugins, "installed", lambda dist: None)
    with pytest.raises(CalibrationError, match="pip install uq-physicell"):
        plugins.uq_physicell(p, {})
    with pytest.raises(CalibrationError, match="pip install fitmulticell"):
        plugins.fitmulticell(p, {})
    monkeypatch.setattr(plugins, "installed", lambda dist: "0.0.9")
    with pytest.raises(CalibrationError, match="does not bridge"):
        plugins.fitmulticell(p, {})
