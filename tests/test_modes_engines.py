"""The modes in each simulator: a small ensemble and a two-point sweep of a translated model.

Each test runs only where its engine is installed (natively or as its Docker image) and is skipped otherwise.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from abmfact.engines import get_engine
from abmfact.modes.ensemble import run_ensemble
from abmfact.modes.sweep import load_sweep, run_sweep

ROOT = Path(__file__).parent.parent
MODEL = ROOT / "examples" / "ir" / "monolayer.yaml"
SHORT = {"experiment.max_time": 120, "experiment.save_interval": 60}


def _engine_or_skip(name: str) -> None:
    avail = get_engine(name).availability()
    if not avail.ok:
        pytest.skip(f"{name} cannot run here: {avail.detail}")


def _ensemble_and_sweep(name: str, tmp_path: Path) -> None:
    _engine_or_skip(name)
    ens = run_ensemble(MODEL, name, 2, params=SHORT, metrics=["last(cells)"], jobs=2, workdir=tmp_path / "e")
    assert ens.data["runs"]["failed"] == 0, ens.data["runs"]["errors"]
    assert ens.data["engine"]["tier"] == "T1"
    assert ens.data["metrics"]["last(cells)"]["n"] == 2
    raw = {"model": str(MODEL), "engine": name, "params": SHORT, "metrics": ["last(cells)"], "jobs": 2,
           "parameters": [{"name": "rules[0].half_max", "low": 0.25, "high": 1.0}],
           "design": {"method": "grid", "levels": 2}}  # fmt: skip
    path = tmp_path / "sweep.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    res = run_sweep(load_sweep(path), workdir=tmp_path / "s", store=tmp_path / "store")
    assert res.data["counts"]["failed"] == 0, res.data["counts"]["errors"]
    runs = res.data["runs"]
    assert len({r["run_id"] for r in runs}) == 2  # the two points are different runs
    again = run_sweep(load_sweep(path), workdir=tmp_path / "s2", store=tmp_path / "store")
    assert again.data["counts"]["cached"] == 2


@pytest.mark.physicell
def test_physicell_ensemble_and_sweep(tmp_path):
    _ensemble_and_sweep("physicell", tmp_path)


@pytest.mark.compucell3d
def test_compucell3d_ensemble_and_sweep(tmp_path):
    _ensemble_and_sweep("compucell3d", tmp_path)


@pytest.mark.morpheus
def test_morpheus_ensemble_and_sweep(tmp_path):
    _ensemble_and_sweep("morpheus", tmp_path)
