"""IR models audited through the engine adapter, with the v0.1 checks."""

from __future__ import annotations

import copy

import pytest
import yaml

from abmfact import cli
from abmfact.adapters import AdapterError, load_adapter
from abmfact.audit import run_audit
from abmfact.config import load_audit, parse_audit
from abmfact.engines.model_adapter import EngineModelAdapter
from abmfact.knobs import Knobs

MODEL = {
    "schema_version": "0.1",
    "name": "tiny",
    "space": {"bounds": {"x_min": -60, "x_max": 60, "y_min": -60, "y_max": 60}, "voxel": {"dx": 20, "dy": 20},
              "boundary": "periodic"},
    "cell_types": [{"name": "a", "cycle": {"rates": [0.004]}}],
    "initial_conditions": {"placements": [{"kind": "random", "cell_type": "a", "count": 10}]},
    "observables": [{"name": "cells", "kind": "live_count"}],
    "experiment": {"max_time": 180, "dt_diffusion": 1, "dt_mechanics": 1, "dt_phenotype": 6, "save_interval": 60},
}  # fmt: skip


@pytest.fixture
def model_file(tmp_path):
    p = tmp_path / "tiny.yaml"
    p.write_text(yaml.safe_dump(MODEL))
    return p


def test_engine_adapter_outputs_and_support(model_file):
    a = EngineModelAdapter("reference", model_file)
    assert a.support.grid and a.support.dx_scale and a.support.update_order[0] == "random"
    out = a.run({}, 1, Knobs())
    assert out["time"] == [0.0, 60.0, 120.0, 180.0] and out["cells"][0] == 10.0
    assert a.describe()["engine"] == "reference"
    closed = copy.deepcopy(MODEL)
    closed["space"]["boundary"] = "closed"
    model_file.write_text(yaml.safe_dump(closed))
    assert not EngineModelAdapter("reference", model_file).support.grid


def test_engine_adapter_with_a_store_and_failures(model_file, tmp_path):
    a = EngineModelAdapter("reference", model_file, store=tmp_path / "store")
    first = a.run({}, 2, Knobs())
    assert a.run({}, 2, Knobs()) == first
    assert len(list((tmp_path / "store" / "runs").glob("*/*/record.json"))) == 1
    with pytest.raises(AdapterError):
        a.run({"cell_types[a].volume": -1}, 1, Knobs())
    with pytest.raises(AdapterError, match="needs"):
        load_adapter({"adapter": "engine", "engine": "reference"})


def test_audit_of_an_ir_model(model_file, capsys):
    doc = {
        "model": {"adapter": "engine", "engine": "reference", "model": model_file.name},
        "claims": [{"id": "C1", "metric": "last(cells)", "test": "greater_than", "value": 10,
                    "skip_checks": {"population_scale": "extensive"}}],
        "budget": {"seeds": 6, "checks": ["determinism", "grid", "time_step", "population_scale"]},
    }  # fmt: skip
    audit_path = model_file.parent / "audit.yaml"
    audit_path.write_text(yaml.safe_dump(doc))
    audit = load_audit(audit_path)
    res = run_audit(audit)
    m = res.matrix()["C1"]
    assert m["determinism"] == "ROBUST" and m["population_scale"] == "SKIPPED"
    assert m["grid"] in ("ROBUST", "INCONCLUSIVE") and m["time_step"] in ("ROBUST", "INCONCLUSIVE")
    assert res.data["audit"]["model"]["engine"] == "reference"
    assert parse_audit(doc, base_dir=model_file.parent).adapter.support.dt_scale


def test_init_engine_template(capsys):
    assert cli.main(["init", "-o", "-", "--adapter", "engine"]) == 0
    assert "engine: reference" in capsys.readouterr().out
