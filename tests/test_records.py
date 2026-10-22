from __future__ import annotations

import math

import numpy as np
import pytest

from abmfact.ir import model_from_dict
from abmfact.records import FidelityReport, FieldFrame, RunOutputs, RunRecord, RunRequest, cells_table
from abmfact.records.observables import compute_observables

MODEL = {
    "name": "m",
    "space": {"bounds": {"x_min": -10, "x_max": 10, "y_min": -10, "y_max": 10}, "voxel": {"dx": 5, "dy": 5}},
    "substrates": [{"name": "o2", "diffusion_coefficient": 1}],
    "cell_types": [{"name": "a"}, {"name": "b"}],
    "observables": [
        {"name": "n", "kind": "cell_count"},
        {"name": "live", "kind": "live_count"},
        {"name": "dead", "kind": "dead_count"},
        {"name": "frac_b", "kind": "type_fraction", "cell_type": "b"},
        {"name": "mx", "kind": "mean_position", "axis": "x"},
        {"name": "mx_b", "kind": "mean_position", "cell_type": "b"},
        {"name": "rg", "kind": "radius_of_gyration"},
        {"name": "o2_mean", "kind": "substrate_mean", "substrate": "o2"},
        {"name": "o2_min", "kind": "substrate_min", "substrate": "o2"},
        {"name": "o2_max", "kind": "substrate_max", "substrate": "o2"},
    ],
    "experiment": {"max_time": 10},
}


def outputs() -> RunOutputs:
    cells = cells_table(
        {
            "time": [0, 0, 0, 10, 10],
            "id": [0, 1, 2, 0, 1],
            "type": [0, 1, 1, 0, 1],
            "x": [-1.0, 1.0, 3.0, 0.0, 4.0],
            "y": [0.0, 0.0, 0.0, 0.0, 0.0],
            "volume": [1, 1, 1, 1, 1],
            "state": [0, 0, 2, 0, 1],
        }
    )
    frames = [FieldFrame(t, "o2", (-7.5, -7.5), (5.0, 5.0), np.full((4, 4), v)) for t, v in ((0, 1.0), (10, 0.5))]
    frames[1].values[0, 0] = 0.1
    return RunOutputs(cells=cells, fields=frames, type_names=["a", "b"])


def test_observables_from_the_common_schema():
    m = model_from_dict(MODEL)
    out = compute_observables(m, outputs())
    s = out.scalars
    assert s["time"] == [0.0, 10.0]
    assert s["n"] == [3, 2] and s["live"] == [2, 1] and s["dead"] == [1, 1]
    assert s["frac_b"] == [0.5, 0.0]
    assert s["mx"] == [0.0, 0.0] and s["mx_b"][0] == 1.0 and math.isnan(s["mx_b"][1])
    assert s["rg"][0] == pytest.approx(1.0) and s["rg"][1] == 0.0
    assert s["o2_min"] == [1.0, 0.1] and s["o2_max"] == [1.0, 0.5]
    assert s["o2_mean"][1] == pytest.approx((15 * 0.5 + 0.1) / 16)
    t, v = out.observable("live")
    assert t.tolist() == [0.0, 10.0] and v.tolist() == [2.0, 1.0]
    assert out.observables["name"].size == 20


def test_cells_table_checks_columns():
    with pytest.raises(ValueError, match="needs column"):
        cells_table({"x": [1.0]})
    with pytest.raises(ValueError, match="shape"):
        cells_table({c: [0] for c in ("time", "id", "type", "x", "y", "volume")} | {"state": [0, 0]})
    assert cells_table({c: [0] for c in ("time", "id", "type", "x", "y", "volume", "state")})["z"].tolist() == [0.0]


def test_missing_field_or_type_raises():
    m = model_from_dict(MODEL)
    out = outputs()
    out.fields = []
    with pytest.raises(KeyError):
        compute_observables(m, out)
    out = outputs()
    out.type_names = ["a"]
    with pytest.raises(KeyError):
        compute_observables(m, out)


def test_request_key_and_record_round_trip():
    r1 = RunRequest("reference", "abc", {"b": 1, "a": 2}, 3, {"dt_scale": 0.5})
    r2 = RunRequest("reference", "abc", {"a": 2, "b": 1}, 3, {"dt_scale": 0.5})
    assert r1.key() == r2.key() and len(r1.key()) == 64
    assert RunRequest("reference", "abc", {"a": 2, "b": 1}, 4, {"dt_scale": 0.5}).key() != r1.key()
    rec = RunRecord(r1, wall_time=1.5, output_paths={"cells": "cells.npz"}, warnings=["w"])
    again = RunRecord.from_dict(rec.to_dict())
    assert again == rec and again.run_id == r1.key()


def test_fidelity_report():
    rep = FidelityReport("physicell:template", "abm-ir", tier="T1")
    rep.add("space.bounds", "exact")
    rep.add("custom_modules/custom.cpp", "native", "custom C++ is not translated")
    assert rep.counts() == {"exact": 1, "approximated": 0, "dropped": 0, "native": 1}
    assert not rep.lossless
    assert "| `custom_modules/custom.cpp` | native |" in rep.to_markdown()
    assert rep.to_dict()["tier_meaning"].startswith("structural")
