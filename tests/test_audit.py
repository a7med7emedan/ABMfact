from __future__ import annotations

import json

import numpy as np
import pytest

from abmfact import PythonAdapter, parse_audit, run_audit
from abmfact.audit import execute, point_key
from abmfact.claims import parse_claims
from abmfact.config import Audit, Budget


def make(target: str, claims: list[dict], seeds: int = 30, jobs: int = 1, params=None, checks=()) -> Audit:
    return parse_audit(
        {
            "model": {"adapter": "python", "target": target},
            "params": params or {},
            "claims": claims,
            "budget": {"seeds": seeds, "jobs": jobs, "checks": list(checks)},
        }
    )


GT9 = {"id": "C1", "statement": "x is large", "metric": "x", "test": "greater_than", "value": 9.0}


def test_correct_model_is_robust_everywhere():
    res = run_audit(make("toymodels:noisy_model", [GT9]))
    assert {v["verdict"] for v in res.verdicts} == {"ROBUST"}
    assert not res.any_flagged and res.flagged_checks() == []
    assert res.data["summary"]["ROBUST"] == 7
    assert res.data["diagnosis"] == []


def test_order_bug_is_shifted_and_diagnosed():
    res = run_audit(make("toymodels:order_bug_model", [GT9]))
    m = res.matrix()["C1"]
    assert m["update_order"] == "SHIFTED"
    assert all(v == "ROBUST" for k, v in m.items() if k != "update_order")
    assert res.flagged_checks() == ["update_order"]
    assert "Stale" in res.data["diagnosis"][0]["cause"]
    row = next(v for v in res.verdicts if v["check"] == "update_order")
    assert row["reproduce"][0]["knobs"] == {"update_order": "id"}


def test_flip_is_fragile():
    claim = {"id": "C1", "metric": "x", "test": "less_than", "value": 12.0}
    res = run_audit(make("toymodels:order_bug_model", [claim], checks=["update_order"]))
    assert res.matrix()["C1"]["update_order"] == "FRAGILE"


def test_spread_change_is_shifted():
    res = run_audit(make("toymodels:spread_bug_model", [GT9 | {"value": 0.0}], seeds=200, checks=["time_step"]))
    row = res.verdicts[0]
    assert row["verdict"] == "SHIFTED" and "spread" in row["reason"]


def test_constant_model_fails_rng_and_is_inconclusive_elsewhere():
    res = run_audit(make("toymodels:constant_model", [GT9]))
    m = res.matrix()["C1"]
    assert m["rng_streams"] == "FRAGILE"
    assert m["determinism"] == "ROBUST"
    assert m["grid"] == "INCONCLUSIVE"


def test_baseline_failure_skip_and_not_testable():
    claims = [
        {"id": "F", "metric": "x", "test": "greater_than", "value": 20.0},
        GT9 | {"id": "S", "skip_checks": {"grid": "not spatial"}},
    ]
    res = run_audit(make("toymodels:bare_noisy", claims))
    m = res.matrix()
    assert m["F"]["update_order"] == "NOT_TESTABLE"
    assert m["F"]["rng_streams"] == "BASELINE_FAILS"
    assert m["S"]["grid"] == "SKIPPED"
    assert m["S"]["rng_streams"] == "ROBUST"


def test_uncertain_claim_reports_seeds_needed():
    claim = {"id": "U", "metric": "x", "test": "greater_than", "value": 10.0}
    res = run_audit(make("toymodels:noisy_model", [claim], params={"mu": 10.05}, checks=["grid"]))
    row = res.verdicts[0]
    assert row["verdict"] == "INCONCLUSIVE" and row["seeds_needed"] > 30


def test_undetermined_equivalence_reports_seeds_needed():
    claim = GT9 | {"value": 0.0, "tolerance": 0.25}
    res = run_audit(make("toymodels:noisy_model", [claim], seeds=10, checks=["grid"]))
    row = res.verdicts[0]
    assert row["verdict"] == "INCONCLUSIVE" and row["seeds_needed"] > 10


def test_monotone_claims():
    inc = {"id": "M", "metric": "x", "test": "monotone_increasing", "over": "a", "where": {"a": [0, 1, 2]}}
    dec = inc | {"id": "D", "test": "monotone_decreasing"}
    res = run_audit(make("toymodels:noisy_model", [inc, dec], params={"slope": 2.0}, checks=["update_order"]))
    m = res.matrix()
    assert m["M"]["update_order"] == "ROBUST"
    assert m["D"]["update_order"] == "BASELINE_FAILS"
    flat = run_audit(make("toymodels:noisy_model", [inc], params={"slope": 0.0}, seeds=10, checks=["grid"]))
    assert flat.verdicts[0]["verdict"] == "INCONCLUSIVE"


def test_series_metric_and_between():
    claim = {"id": "B", "metric": "mean(series)", "test": "between", "value": [9, 11]}
    res = run_audit(make("toymodels:noisy_model", [claim], checks=["determinism", "rng_streams"]))
    assert {v["verdict"] for v in res.verdicts} == {"ROBUST"}


def test_results_are_reproducible_and_parallel_matches_serial():
    a = run_audit(make("toymodels:order_bug_model", [GT9], seeds=12))
    b = run_audit(make("toymodels:order_bug_model", [GT9], seeds=12, jobs=2))
    da, db = json.loads(a.to_json(include_timestamp=False)), json.loads(b.to_json(include_timestamp=False))
    assert da["audit"]["budget"].pop("jobs") == 1 and db["audit"]["budget"].pop("jobs") == 2
    assert da == db
    again = run_audit(make("toymodels:order_bug_model", [GT9], seeds=12))
    assert again.to_json(include_timestamp=False) == a.to_json(include_timestamp=False)
    data = json.loads(a.to_json())
    assert data["schema_version"] == "1" and "created" in data


def test_execute_falls_back_to_serial_for_unpicklable_adapters():
    from abmfact import PythonAdapter

    adapter = PythonAdapter(lambda p, s, k: {"x": s})
    calls = []
    jobs = [("baseline", "{}", {}, s, {}) for s in range(3)]
    out = execute(adapter, jobs, workers=2, progress=lambda i, n: calls.append((i, n)))
    assert out[("baseline", "{}", 2)] == {"x": 2.0}
    assert calls[-1] == (3, 3)


def test_point_key_is_order_independent():
    assert point_key({"b": 1, "a": 2}) == point_key({"a": 2, "b": 1})


def test_direct_audit_object():
    from toymodels import noisy_model

    audit = Audit(noisy_model, {}, parse_claims([GT9]), Budget(seeds=5, checks=("determinism",)))
    assert run_audit(audit).verdicts[0]["verdict"] == "ROBUST"


def test_nan_outputs_become_null():
    from abmfact import PythonAdapter
    from abmfact.audit import _clean

    assert _clean({"a": [float("nan"), 1.0, float("inf")]}) == {"a": [None, 1.0, None]}
    assert PythonAdapter(lambda p, s, k: {"x": 1}).run({}, 1, __import__("abmfact").Knobs()) == {"x": 1.0}
    with pytest.raises(TypeError):
        __import__("abmfact.audit", fromlist=["_json_default"])._json_default(object())


def test_tolerance_warning_for_near_zero_baselines(tmp_path):
    from abmfact import stats as st
    from abmfact.report import render_html

    rng = np.random.default_rng(0)
    # Tiny mean, tiny noise: the 10% tolerance is a tiny absolute margin.
    tiny = 0.003 + rng.normal(0, 3e-5, 20)
    assert "near zero" in st.tolerance_warning(tiny, st.default_tolerance(tiny))
    # Noisy mean: the 10% tolerance is below the standard error, whatever the units.
    noisy = 5.0 + rng.normal(0, 10.0, 20)
    assert "standard error" in st.tolerance_warning(noisy, st.default_tolerance(noisy))
    assert st.tolerance_warning(10 + rng.normal(0, 1, 20), 1.0) is None

    small = PythonAdapter(lambda p, s, k: {"x": 0.003 + 1e-4 * np.random.default_rng(s).normal()}, supports=None)
    claim = {"id": "C1", "metric": "x", "test": "greater_than", "value": 0}
    res = run_audit(Audit(small, {}, parse_claims([claim]), Budget(seeds=8, checks=("rng_streams",))))
    assert "near zero" in res.data["claims"][0]["warnings"][0]
    assert res.verdicts[0]["warnings"] == res.data["claims"][0]["warnings"]
    assert "Tolerance warning" in render_html(res)
    explicit = run_audit(
        Audit(small, {}, parse_claims([{**claim, "tolerance": 0.01}]), Budget(seeds=8, checks=("rng_streams",)))
    )
    assert "warnings" not in explicit.data["claims"][0] and "warnings" not in explicit.verdicts[0]
