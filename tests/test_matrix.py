"""The validation: each planted fault is caught by its own check and by no other."""

from __future__ import annotations

import os

import pytest

from abmfact.demo import run_demo


@pytest.mark.slow
def test_planted_fault_matrix_is_diagonal():
    res = run_demo(seeds=100, jobs=max(1, os.cpu_count() or 1))
    assert res.problems() == [], "\n" + res.table()
    clean = res.results["clean"]
    assert {v["verdict"] for v in clean.verdicts} <= {"ROBUST", "SKIPPED"}
