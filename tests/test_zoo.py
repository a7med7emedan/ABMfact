from __future__ import annotations

import math

import pytest

from abmfact import zoo
from abmfact.knobs import Knobs, perm_seed


def run(fault=None, seed=3, **knobs):
    return zoo.simulate({}, seed, Knobs(**knobs), fault=fault)


def test_outputs_shape():
    out = run()
    assert set(out) == {"prevalence", "attack_rate", "peak_prevalence", "peak_time"}
    assert len(out["prevalence"]) == zoo.DEFAULT_PARAMS["T"]
    assert 0 < out["attack_rate"] <= 1 - zoo.DEFAULT_PARAMS["immune"] + 0.02
    assert out["peak_prevalence"] == max(out["prevalence"])


def test_clean_model_is_exactly_invariant_to_world_placement():
    base = run()
    for knobs in ({"grid_offset": (0.5, 0.5)}, {"grid_offset": (0.25, 0.0)}, {"grid_rotation": 90}):
        assert run(**knobs) == base


def test_clean_synchronous_equals_random_and_seeds_matter():
    assert run(update_order="synchronous") == run()
    assert run(seed=4) != run(seed=3)
    assert run(update_order="id") != run()


@pytest.mark.parametrize("fault", ["stale_state", "dt_unscaled", "density"])
def test_faults_are_invisible_at_baseline(fault):
    assert run(fault=fault) == run()


def test_each_fault_shows_under_its_knob():
    assert run(fault="stale_state", update_order="synchronous") != run(update_order="synchronous")
    assert run(fault="dt_unscaled", dt_scale=0.5) != run(dt_scale=0.5)
    assert run(fault="density", pop_scale=2.0) != run(pop_scale=2.0)
    assert run(fault="boundary", grid_offset=(0.5, 0.5)) != run(fault="boundary")
    assert run(fault="ignored_seed", seed=1) == run(fault="ignored_seed", seed=2)
    shuffled = {"agent_order": "shuffled", "agent_perm_seed": perm_seed(3)}
    assert run(fault="id_dependence", **shuffled) != run(fault="id_dependence")


def test_nondeterminism_fault_differs_between_identical_calls():
    outs = [run(fault="nondeterminism") for _ in range(3)]
    assert any(o != outs[0] for o in outs[1:])


def test_errors_and_runner():
    with pytest.raises(ValueError):
        run(fault="nope")
    with pytest.raises(ValueError):
        run(update_order="sideways")
    with pytest.raises(ValueError):
        zoo.ZooRunner("nope")
    with pytest.raises(ValueError):
        zoo.audit_document("nope")
    assert repr(zoo.ZooRunner("boundary")) == "ZooRunner('boundary')"
    assert zoo.MODELS["clean"].run({}, 1, Knobs()) == run(seed=1)
    doc = zoo.audit_document("boundary", seeds=5)
    assert doc["model"]["target"] == "abmfact.zoo:fault_boundary" and doc["budget"]["seeds"] == 5


def test_extinction_pads_prevalence():
    out = zoo.simulate({"beta": 0.0, "beta_g": 0.0, "gamma": 5.0}, 1, Knobs())
    assert len(out["prevalence"]) == zoo.DEFAULT_PARAMS["T"]
    assert out["prevalence"][-1] == 0.0


def test_field_fault_is_invisible_until_dt_and_dx_are_refined_together():
    small = {"T": 60, "n0": 10}
    for knobs in (Knobs(), Knobs(dt_scale=0.5), Knobs(agent_order="shuffled", agent_perm_seed=3)):
        assert zoo.simulate_field(small, 2, knobs) == zoo.simulate_field(small, 2, knobs, fault="unstable_diffusion")
    refined = Knobs(dt_scale=0.5, dx_scale=0.5)
    clean = zoo.simulate_field(small, 2, refined)
    broken = zoo.simulate_field(small, 2, refined, fault="unstable_diffusion")
    assert 0 < clean["nutrient_mean"] < 1
    blown = broken["nutrient_mean"]
    assert not math.isfinite(blown) or abs(blown) > 1e100  # the checkerboard mode grows every step
    assert zoo.simulate_field(small, 2, Knobs()) != zoo.simulate_field(small, 3, Knobs())
    assert zoo.simulate_field(small, 2, Knobs(rng_streams="independent")) != zoo.simulate_field(small, 2, Knobs())


def test_field_models_and_documents():
    with pytest.raises(ValueError):
        zoo.simulate_field({}, 1, Knobs(), fault="nope")
    with pytest.raises(ValueError):
        zoo.FieldRunner("nope")
    assert repr(zoo.FieldRunner("unstable_diffusion")) == "FieldRunner('unstable_diffusion')"
    doc = zoo.audit_document("unstable_diffusion", seeds=5)
    assert doc["model"]["target"] == "abmfact.zoo:fault_unstable_diffusion" and doc["claims"][0]["id"] == "F1"
    assert zoo.audit_document("field_clean")["model"]["target"] == "abmfact.zoo:field_clean"
    assert zoo.EXPECTED_CHECK["unstable_diffusion"] == "convergence"
    assert zoo.EXPECTED_ALSO == {"dt_unscaled": ("convergence",)}


def test_expected_matrix_and_table():
    from abmfact.checks import CHECK_NAMES
    from abmfact.demo import DemoResult, expected

    assert expected("dt_unscaled") == {"time_step", "convergence"} and expected("clean") == set()
    flags = {m: {c: c in expected(m) for c in (*CHECK_NAMES, "convergence")} for m in zoo.MODELS}
    res = DemoResult(flags, {})
    assert res.problems() == [] and res.columns()[-1] == "convergence"
    assert "CAUGHT" in res.table().splitlines()[zoo.FAULTS.index("dt_unscaled") + 2]
    flags["dt_unscaled"]["convergence"] = False
    flags["field_clean"]["grid"] = True
    assert DemoResult(flags, {}).problems() == [
        "dt_unscaled: missed by convergence",
        "field_clean: false alarm from grid",
    ]
