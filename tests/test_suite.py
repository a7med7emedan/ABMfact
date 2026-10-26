from __future__ import annotations

from pathlib import Path

import pytest

from abmfact.config import load_audit

SUITE = Path(__file__).parent.parent / "examples" / "suite"
PROBLEMS = ("monolayer", "spheroid", "sorting", "chemotaxis", "drug_pulse", "immune")


def test_the_six_reference_problems_are_present():
    assert sorted(p.name.removesuffix(".audit.yaml") for p in SUITE.glob("*.audit.yaml")) == sorted(PROBLEMS)


@pytest.mark.parametrize("problem", PROBLEMS)
def test_each_problem_loads_in_the_reference_engine_with_two_claims(problem):
    audit = load_audit(SUITE / f"{problem}.audit.yaml")
    assert len(audit.claims) == 2
    assert audit.model_cfg["engine"] == "reference"
