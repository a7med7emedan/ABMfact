"""The reference engine: rules, determinism, knobs, refusals and the three example models."""

from __future__ import annotations

import copy
import json

import numpy as np
import pytest

from abmfact import cli
from abmfact.engines import EngineError, get_engine
from abmfact.engines.reference import ReferenceEngine
from abmfact.ir import apply_params, load_model, model_from_dict
from abmfact.knobs import Knobs
from abmfact.reference import refusals, simulate
from abmfact.reference.phenotype import Ruleset, base_value, build_rulesets

BASE = {
    "name": "t",
    "space": {
        "bounds": {"x_min": -100, "x_max": 100, "y_min": -100, "y_max": 100},
        "voxel": {"dx": 20, "dy": 20},
        "boundary": "periodic",
    },
    "substrates": [{"name": "oxygen", "diffusion_coefficient": 1000, "initial_value": 10}],
    "cell_types": [
        {"name": "a", "cycle": {"rates": [0.001]}, "secretion": [{"substrate": "oxygen", "uptake_rate": 1}]},
        {"name": "b"},
    ],
    "initial_conditions": {"placements": [{"kind": "random", "cell_type": "a", "count": 30}]},
    "observables": [
        {"name": "n", "kind": "live_count"},
        {"name": "o2", "kind": "substrate_mean", "substrate": "oxygen"},
    ],
    "experiment": {"max_time": 120, "dt_diffusion": 1, "dt_mechanics": 0.5, "dt_phenotype": 6, "save_interval": 30},
}


def model(**changes):
    d = copy.deepcopy(BASE)
    d.update(changes)
    return model_from_dict(d)


def rule(signal, direction, behaviour, sat, half, hill, dead=False):
    return {"cell_type": "a", "signal": signal, "direction": direction, "behaviour": behaviour,
            "saturation_value": sat, "half_max": half, "hill_power": hill, "apply_to_dead": dead}  # fmt: skip


def test_rule_combination_matches_a_hand_computed_case():
    m = model(
        rules=[
            rule("oxygen", "increases", "cycle entry", 0.003, 10, 2),
            rule("pressure", "decreases", "cycle entry", 0.0, 1, 1),
            rule("volume", "increases", "cycle entry", 0.002, 1000, 1),
        ]
    )
    rs = build_rulesets(m)[("a", "cycle entry")]
    assert rs.base == 0.001 and rs.max_value == 0.003 and rs.min_value == 0.0
    # oxygen 10 -> (10/10)^2 = 1; volume 2000 -> (2000/1000)^1 = 2; S = 3, U = 3/4.
    # b_up = 0.001 + (0.003 - 0.001) * 0.75 = 0.0025. Pressure 1 -> D = 1/2.
    # b = 0.0025 + (0 - 0.0025) * 0.5 = 0.00125.
    signals = {"oxygen": np.array([10.0, 0.0]), "pressure": np.array([1.0, 0.0]), "volume": np.array([2000.0, 0.0])}
    out = rs.evaluate(signals, np.array([False, False]), np.array([9.0, 9.0]))
    assert out[0] == pytest.approx(0.00125, rel=1e-12)
    assert out[1] == pytest.approx(0.001)  # no signal: the base value
    # A dead cell sees 0 for rules that do not apply to the dead; with no rule applying it keeps its value.
    assert rs.evaluate(signals, np.array([True, True]), np.array([7.0, 8.0])).tolist() == [7.0, 8.0]
    dead_rule = Ruleset("necrosis", 0.0, up=[], down=[])
    assert dead_rule.evaluate({}, np.array([False]), np.array([5.0])).tolist() == [5.0]


def test_base_values():
    m = model()
    a = m.cell_type("a")
    assert base_value(a, "cycle entry") == 0.001 and base_value(a, "oxygen uptake") == 1.0
    assert base_value(a, "oxygen secretion target") == 1.0 and base_value(a, "oxygen secretion") == 0.0
    assert base_value(m.cell_type("b"), "oxygen secretion target") == 1.0
    assert base_value(a, "migration persistence time") == 1.0 and base_value(a, "unknown") == 0.0


def test_same_seed_same_run_and_seeds_matter():
    m = model()
    a, b = simulate(m, 3), simulate(m, 3)
    for c in a.cells:
        np.testing.assert_array_equal(a.cells[c], b.cells[c])
    assert a.scalars == b.scalars
    assert simulate(m, 4).scalars != a.scalars
    assert simulate(m, 3, Knobs(rng_streams="independent")).scalars != a.scalars


def test_update_orders():
    m = model()
    base = simulate(m, 2)
    assert simulate(m, 2, Knobs(update_order="synchronous")).scalars == base.scalars
    assert simulate(m, 2, Knobs(update_order="id")).cells["x"].tolist() != base.cells["x"].tolist()
    assert simulate(m, 2, Knobs(update_order="reverse")).scalars["n"][0] == 30


def test_grid_knobs_move_the_world_but_not_the_results():
    m = model()
    base = simulate(m, 5)
    for knobs in (Knobs(grid_offset=(0.5, 0.5)), Knobs(grid_offset=(0.25, 0.0)), Knobs(grid_rotation=90)):
        moved = simulate(m, 5, knobs)
        assert moved.scalars["n"] == base.scalars["n"]
        # Rounding differs in the moved frame, so a few cells can land in a neighbouring voxel.
        np.testing.assert_allclose(moved.scalars["o2"], base.scalars["o2"], rtol=0.02)
        if knobs.grid_rotation == 0:
            np.testing.assert_allclose(moved.cells["x"], base.cells["x"], atol=1e-3)


def test_agent_order_population_and_resolution_knobs():
    m = model()
    base = simulate(m, 1)
    shuffled = simulate(m, 1, Knobs(agent_order="shuffled", agent_perm_seed=9))
    assert sorted(shuffled.cells["x"][:30]) == pytest.approx(sorted(base.cells["x"][:30]))
    assert simulate(m, 1, Knobs(pop_scale=2.0)).scalars["n"][0] == 60
    fine = simulate(m, 1, Knobs(dx_scale=0.5))
    assert fine.fields[0].values.shape == (20, 20) and base.fields[0].values.shape == (10, 10)
    half = simulate(m, 1, Knobs(dt_scale=0.5))
    assert half.save_times == base.save_times


def test_refusals_name_the_reason():
    assert refusals(model(), Knobs()) == []
    three_d = copy.deepcopy(BASE)
    three_d["space"]["dimensions"] = 3
    assert "2D only" in refusals(model_from_dict(three_d), Knobs())[0]
    immune = copy.deepcopy(BASE)
    immune["cell_types"][1]["interactions"] = {"attack_rates": {"a": 0.1}}
    assert "milestone 3" in " ".join(refusals(model_from_dict(immune), Knobs()))
    assert refusals(model(rules=[rule("intracellular oxygen", "increases", "apoptosis", 1, 1, 1)]), Knobs())
    assert refusals(model(rules=[rule("oxygen", "increases", "attack b", 1, 1, 1)]), Knobs())
    closed = copy.deepcopy(BASE)
    closed["space"]["boundary"] = "closed"
    assert "periodic" in refusals(model_from_dict(closed), Knobs(grid_offset=(0.5, 0.5)))[0]
    explicit = model(
        initial_conditions={"placements": [{"kind": "positions", "cell_type": "a", "positions": [[0, 0]]}]}
    )
    assert "pop_scale" in refusals(explicit, Knobs(pop_scale=2.0))[0]
    others = model(
        cell_types=[{"name": "a", "cycle": {"model": "ki67_basic", "rates": [0.1, 0.1]}, "transformations": {"a": 1}},
                    {"name": "b"}],
        events=[{"time": 1, "action": "set_parameter", "target": "x", "value": 1}],
        intracellular=[{"cell_type": "a", "format": "maboss", "files": {"bnd": "x.bnd"}}],
        native_extensions=[{"engine": "physicell", "kind": "custom_cpp"}],
    )  # fmt: skip
    assert len(refusals(others, Knobs(update_order="sideways"))) == 6
    with pytest.raises(ValueError):
        simulate(three_d and model_from_dict(three_d), 1)
    with pytest.raises(ValueError, match="solver"):
        simulate(model(), 1, solver="magic")


def test_events_positions_and_explicit_solver(tmp_path):
    csv = tmp_path / "cells.csv"
    csv.write_text("x,y,z,type\n0,0,0,a\n10,0,0,1\n")
    closed = copy.deepcopy(BASE)
    closed["space"]["boundary"] = "closed"
    closed["substrates"][0]["initial_value"] = 0
    closed["initial_conditions"] = {"placements": [{"kind": "positions_csv", "path": "cells.csv"}]}
    closed["events"] = [
        {"time": 30, "until": 60, "action": "set_substrate_boundary", "target": "oxygen", "value": 5},
        {"time": 90, "action": "add_cells", "target": "b", "value": 4},
        {"time": 100, "action": "set_substrate", "target": "oxygen", "value": 0},
    ]
    (tmp_path / "m.json").write_text(json.dumps(closed))
    m = load_model(tmp_path / "m.json")
    out = simulate(m, 1)
    o2 = dict(zip(out.save_times, out.scalars["o2"], strict=True))
    assert o2[30.0] == 0.0 and o2[60.0] > 0.5 and o2[120.0] < o2[60.0]
    assert out.scalars["n"][0] == 2 and out.scalars["n"][-1] >= 6
    assert out.cells_at(0.0)["type"].tolist() == [0, 1]
    explicit = simulate(m, 1, solver="explicit")
    np.testing.assert_allclose(explicit.scalars["o2"], out.scalars["o2"], rtol=0.05, atol=0.05)
    periodic = copy.deepcopy(BASE)
    periodic["events"] = [{"time": 0, "action": "set_substrate_boundary", "target": "oxygen", "value": 1}]
    assert "periodic" in simulate(model_from_dict(periodic), 1).warnings[0]


def test_engine_contract_and_registry():
    engine = get_engine("reference")
    assert isinstance(engine, ReferenceEngine)
    caps = engine.capabilities()
    assert caps.dimensions == (2,) and caps.diffusion and "dx_scale" in caps.knobs
    m = model()
    rec = engine.run_once(m, {"experiment.max_time": 60}, 1)
    assert rec.status == "ok" and rec.outputs.save_times[-1] == 60 and rec.request.params == {"experiment.max_time": 60}
    assert rec.request.engine_version.startswith("abmfact ")
    assert engine.refuse("no-such-file.yaml", Knobs())
    with pytest.raises(EngineError):
        engine.prepare(42, {}, Knobs(), 1)
    three_d = copy.deepcopy(BASE)
    three_d["space"]["dimensions"] = 3
    with pytest.raises(EngineError, match="2D"):
        engine.run_once(model_from_dict(three_d))


def test_contact_inhibition_slows_monolayer_growth():
    m = apply_params(load_model("examples/ir/monolayer.yaml"), {"experiment.max_time": 2160})
    inhibited = simulate(m, 1).scalars["cells"]
    free = simulate(apply_params(m, {"rules[0].half_max": 1e9}), 1).scalars["cells"]
    assert inhibited[-1] < 0.8 * free[-1]


def test_spheroid_core_runs_short_of_oxygen():
    m = apply_params(load_model("examples/ir/spheroid.yaml"), {"experiment.max_time": 720})
    s = simulate(m, 1).scalars
    assert min(s["oxygen_min"]) < 10 and max(s["dead"]) > 0


def test_chemotaxis_climbs_the_gradient():
    m = apply_params(load_model("examples/ir/chemotaxis.yaml"), {"experiment.max_time": 360})
    biased = simulate(m, 1).scalars["mean_x"]
    unbiased = simulate(apply_params(m, {"cell_types[leukocyte].motility.migration_bias": 0.0}), 1).scalars["mean_x"]
    assert biased[-1] - biased[0] > 300 and abs(unbiased[-1] - unbiased[0]) < 100


def test_run_model_command(tmp_path, capsys):
    out = tmp_path / "run"
    args = ["run-model", "examples/ir/chemotaxis.yaml", "--out", str(out), "--seed", "2",
            "--params", '{"experiment.max_time": 60}', "--knobs", '{"dt_scale": 0.5}']  # fmt: skip
    assert cli.main(args) == 0
    text = capsys.readouterr().out
    assert "reference: ok" in text and "mean_x" in text
    record = json.loads((out / "record.json").read_text())
    assert record["request"]["seed"] == 2 and record["request"]["knobs"]["dt_scale"] == 0.5
    store = tmp_path / "store"
    assert cli.main([*args, "--store", str(store), "-q"]) == 0
    assert cli.main([*args, "--store", str(store)]) == 0
    assert "cached" in capsys.readouterr().out
    bad = tmp_path / "bad.yaml"
    bad.write_text("schema_version: '0.1'\nname: x\n")
    assert cli.main(["run-model", str(bad), "--out", str(tmp_path / "x")]) == 2
    assert cli.main(["run-model", "examples/ir/monolayer.yaml", "--engine", "nope"]) == 2
