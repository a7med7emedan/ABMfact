from __future__ import annotations

import json
import math

import numpy as np
import pytest
import yaml

from abmfact import cli
from abmfact.config import parse_audit
from abmfact.verify import ConvergenceSettings, converge, gci
from abmfact.verify.convergence import ConvergenceError, _point_verdict

GT = {"id": "C1", "metric": "x", "test": "greater_than", "value": 5}


def audit(target: str, seeds: int = 12, claims=None, **extra):
    doc = {"model": {"adapter": "python", "target": target}, "claims": claims or [GT],
           "budget": {"seeds": seeds, "jobs": 1}, **extra}  # fmt: skip
    return parse_audit(doc)


def test_gci_on_manufactured_sequences():
    # f(h) = 1 + h^2 at h = 1, 1/2, 1/4: order 2, extrapolated value 1.
    est = gci(1 + 1 / 16, 1 + 1 / 4, 2.0)
    assert est.kind == "monotone" and est.order == pytest.approx(2.0)
    assert est.extrapolated == pytest.approx(1.0)
    assert est.gci_fine == pytest.approx(1.25 * (3 / 16) / (1 + 1 / 16) / 3)
    assert gci(1.0, 2.0, 3.0).kind == "divergent" and gci(1.0, 3.0, 4.0).kind == "divergent"
    osc = gci(1.0, 1.5, 0.0)
    assert osc.kind == "oscillatory" and osc.order == pytest.approx(math.log2(3))
    assert gci(2.0, 2.0, 2.0).kind == "unchanged" and gci(2.0, 2.0, 3.0).gci_fine == 0.0
    assert gci(0.0, 1.0, 3.0).gci_fine is None and gci(1.0, 2.0, 2.0).kind == "divergent"


def _vals(means, sd=0.01, n=20, seed=0):
    rng = np.random.default_rng(seed)
    return [m + rng.normal(0, sd, n) for m in means]


def test_point_verdicts():
    s = ConvergenceSettings()
    ok = _point_verdict(_vals([12.0, 10.5, 10.1]), None, 0.05, s)
    assert ok["verdict"] == "CONVERGED" and ok["established"] and "observed order" in ok["reason"]
    shifted = _point_verdict(_vals([30.0, 20.0, 15.0]), None, 0.05, s)
    assert shifted["verdict"] == "NOT_CONVERGED" and "beyond the tolerance" in shifted["reason"]
    grows = _point_verdict(_vals([10.0, 10.4, 10.9], sd=0.05), 2.0, 0.05, s)
    assert grows["verdict"] == "NOT_CONVERGED" and "grows" in grows["reason"]
    noisy = _point_verdict(_vals([10.0, 10.0, 10.0], sd=5.0, n=5), None, 0.05, s)
    assert noisy["verdict"] == "INCONCLUSIVE" and noisy["seeds_needed"] > 5
    flat = [np.full(5, 10.0), np.full(5, 11.0), np.full(5, 13.0)]
    assert _point_verdict(flat, None, 0.05, s)["verdict"] == "INCONCLUSIVE"
    exact = _point_verdict(flat, None, 0.05, ConvergenceSettings(deterministic=True))
    assert exact["verdict"] == "NOT_CONVERGED" and "grows" in exact["reason"]
    boom = _point_verdict([np.ones(3), np.ones(3), np.array([1.0, np.inf, 1.0])], None, 0.05, s)
    assert boom["verdict"] == "NOT_CONVERGED" and "blew up" in boom["reason"] and boom["levels"][2] is None


def test_converge_end_to_end():
    res = converge(audit("toymodels:refined_model"))
    assert res.by_claim() == {"C1": "CONVERGED"}
    assert [lv["dt_scale"] for lv in res.data["levels"]] == [1.0, 0.5, 0.25]
    assert res.data["settings"]["refined"] == ["dt"]
    point = next(iter(res.verdicts[0]["points"].values()))
    assert point["gci"]["order"] == pytest.approx(2.0, abs=0.3)
    assert converge(audit("toymodels:diverging_model")).by_claim() == {"C1": "NOT_CONVERGED"}
    blow = converge(audit("toymodels:blow_up_model"))
    assert blow.by_claim() == {"C1": "NOT_CONVERGED"} and blow.data["settings"]["refined"] == ["dt", "dx"]
    assert converge(audit("toymodels:bare_noisy")).by_claim() == {"C1": "NOT_TESTABLE"}
    skipped = audit("toymodels:refined_model", claims=[{**GT, "skip_checks": {"convergence": "not a solver output"}}])
    assert converge(skipped).by_claim() == {"C1": "SKIPPED"}
    data = json.loads(res.to_json(include_timestamp=False))
    assert "created" not in data and data["summary"]["CONVERGED"] == 1


def test_settings():
    s = ConvergenceSettings.from_mapping({"refine": ["dt"], "ratio": 3})
    assert s.refine == ("dt",) and s.ratio == 3.0
    for bad in ({"levels": 4}, {"ratio": 1}, {"refine": ["dx"]}, {"nope": 1}):
        with pytest.raises(ConvergenceError):
            ConvergenceSettings.from_mapping(bad)
    a = audit("toymodels:blow_up_model", convergence={"refine": ["dt"]})
    assert converge(a).by_claim() == {"C1": "CONVERGED"}


def test_converge_command(tmp_path, capsys):
    path = tmp_path / "audit.yaml"
    path.write_text(yaml.safe_dump({"model": {"adapter": "python", "target": "toymodels:refined_model"},
                                    "params": {"c": 4.0}, "claims": [GT], "budget": {"seeds": 10}}))  # fmt: skip
    assert cli.main(["converge", str(path), "--out", str(tmp_path / "out"), "--jobs", "1"]) == 0
    text = capsys.readouterr().out
    assert "CONVERGED" in text and "dt x0.25" in text and (tmp_path / "out" / "convergence.json").exists()
    path.write_text(yaml.safe_dump({"model": {"adapter": "python", "target": "toymodels:diverging_model"},
                                    "claims": [GT], "budget": {"seeds": 10}}))  # fmt: skip
    assert cli.main(["converge", str(path), "-q"]) == 1
    assert "not converged: C1" in capsys.readouterr().err
    path.write_text(yaml.safe_dump({"model": {"adapter": "python", "target": "toymodels:refined_model"},
                                    "claims": [GT], "convergence": {"levels": 5}}))  # fmt: skip
    assert cli.main(["converge", str(path)]) == 2
