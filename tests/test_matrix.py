"""The validation: each planted fault is caught by its own check and by no other, apart from one
documented overlap (dt_unscaled is also caught by convergence, since both refine the time step)."""

from __future__ import annotations

import os

import pytest

from abmfact.demo import run_demo


@pytest.mark.slow
def test_planted_fault_matrix_is_diagonal():
    res = run_demo(seeds=100, jobs=max(1, os.cpu_count() or 1))
    assert res.problems() == [], "\n" + res.table()
    assert "convergence" in res.columns()
    clean = res.results["clean"]
    assert {v["verdict"] for v in clean.verdicts} <= {"ROBUST", "SKIPPED"}
    field_clean = res.results["field_clean"]
    assert not {v["verdict"] for v in field_clean.verdicts} & {"FRAGILE", "SHIFTED", "BASELINE_FAILS"}
    for model in ("clean", "field_clean"):
        assert "NOT_CONVERGED" not in res.convergence[model].by_claim().values()
    blown = [v for v in res.convergence["unstable_diffusion"].verdicts if v["verdict"] == "NOT_CONVERGED"]
    assert blown and any("blew up" in v["reason"] for v in blown)
