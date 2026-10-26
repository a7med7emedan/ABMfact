"""Attack and damage in the reference engine, against hand-computed cases from PhysiCell 1.14.2's rules."""

from __future__ import annotations

import copy

import numpy as np
import pytest

from abmfact.engines.physicell.translate import export_project, import_project
from abmfact.ir import model_from_dict
from abmfact.knobs import Knobs
from abmfact.records import APOPTOTIC, LIVE
from abmfact.reference.sim import Simulation

DT_M, DT_P = 0.1, 6.0

BASE = {
    "schema_version": "0.1",
    "name": "attack",
    "space": {
        "dimensions": 2,
        "bounds": {"x_min": -100, "x_max": 100, "y_min": -100, "y_max": 100},
        "voxel": {"dx": 20, "dy": 20},
        "boundary": "closed",
    },
    "cell_types": [
        {
            "name": "effector",
            "mechanics": {"repulsion_strength": 0, "adhesion_strength": 0},
            "interactions": {"attack_rates": {"target": 1e6}, "damage_rate": 2.0, "attack_duration": 1e9},
        },
        {"name": "target", "mechanics": {"repulsion_strength": 0, "adhesion_strength": 0}},
    ],
    "initial_conditions": {
        "placements": [
            {"kind": "positions", "cell_type": "effector", "positions": [[0, 0]]},
            {"kind": "positions", "cell_type": "target", "positions": [[12, 0]]},
        ]
    },
    "experiment": {
        "max_time": 1.0,
        "dt_diffusion": DT_M,
        "dt_mechanics": DT_M,
        "dt_phenotype": DT_P,
        "save_interval": 1.0,
    },
}


def build(**changes):
    d = copy.deepcopy(BASE)
    for path, value in changes.items():
        node = d
        *head, last = path.split(".")
        for k in head:
            node = node[int(k)] if k.isdigit() else node.setdefault(k, {})
        node[last] = value
    return model_from_dict(d)


def run(model, seed=1):
    sim = Simulation(model, seed, Knobs())
    sim.run()
    return sim


def target_of(sim):
    return int(np.flatnonzero(sim.cells.type == 1)[0])


def test_an_attack_adds_damage_rate_times_dt_every_mechanics_step():
    sim = run(build())
    t = target_of(sim)
    steps = round(1.0 / DT_M)
    # A certain attack starts on the first mechanics step and never ends, so damage = rate * dt * steps.
    assert sim.cells.damage[t] == pytest.approx(2.0 * DT_M * steps)
    assert sim.cells.attack_time[t] == pytest.approx(DT_M * steps)
    assert sim.cells.target[np.flatnonzero(sim.cells.type == 0)[0]] == sim.cells.id[t]


def test_no_attack_without_a_rate_contact_or_immunogenicity():
    assert run(build(**{"cell_types.0.interactions.attack_rates": {}})).cells.damage.max() == 0
    far = build(**{"initial_conditions.placements.1.positions": [[80, 0]]})
    assert run(far).cells.damage.max() == 0
    shy = build(**{"cell_types.1.interactions.immunogenicities": {"effector": 0.0}})
    assert run(shy).cells.damage.max() == 0


def test_short_attacks_end_and_restart():
    # An attack lasting one step ends after every step and a certain rate restarts it on the next one,
    # so the damage is the same as one long attack.
    sim = run(build(**{"cell_types.0.interactions.attack_duration": DT_M}))
    assert sim.cells.damage[target_of(sim)] == pytest.approx(2.0 * DT_M * 10)


def test_rare_attacks_follow_the_start_probability():
    # With p = rate * dt for one step, the share of runs where an attack starts is binomial; 4000 fixed seeds
    # put it within four standard deviations of p.
    rate, n = 0.5, 4000
    m = build(**{"cell_types.0.interactions.attack_rates": {"target": rate}, "experiment.max_time": DT_M})
    p = rate * DT_M
    started = np.mean([run(m, seed).cells.attack_time.max() > 0 for seed in range(n)])
    assert started == pytest.approx(p, abs=4 * np.sqrt(p * (1 - p) / n))


def test_damage_grows_and_is_repaired_as_in_advance_damage():
    m = build(**{"cell_types.0.interactions.attack_rates": {}, "cell_types.1.integrity":
                 {"damage_rate": 0.3, "damage_repair_rate": 0.05}, "experiment.max_time": 3 * DT_P})  # fmt: skip
    expected = 0.0
    for _ in range(3):
        expected = (expected + 0.3 * DT_P) / (1 + 0.05 * DT_P)
    assert run(m).cells.damage[1] == pytest.approx(expected)


def test_a_damage_rule_kills_the_target():
    rule = {"cell_type": "target", "signal": "damage", "direction": "increases", "behaviour": "apoptosis",
            "saturation_value": 1e6, "half_max": 0.1, "hill_power": 4}  # fmt: skip
    m = build(rules=[rule], **{"experiment.max_time": 2 * DT_P})
    sim = run(m)
    assert sim.cells.state[target_of(sim)] == APOPTOTIC
    effector = np.flatnonzero(sim.cells.type == 0)[0]
    assert sim.cells.state[effector] == LIVE and sim.cells.target[effector] == -1


def test_attacks_are_deterministic_for_a_seed():
    m = build(**{"cell_types.0.interactions.attack_rates": {"target": 2.0}, "experiment.max_time": 3.0})
    assert run(m, 7).cells.damage.tolist() == run(m, 7).cells.damage.tolist()


def test_physicell_translation_keeps_attack_duration_and_integrity(tmp_path):
    m = build(**{"cell_types.1.integrity": {"damage_rate": 0.0, "damage_repair_rate": 0.1},
                 "cell_types.1.interactions.immunogenicities": {"effector": 0.5}})  # fmt: skip
    report = export_project(m, tmp_path / "project")
    status = {e.path: e.status for e in report.entries}
    assert status["cell_types[target].interactions.immunogenicities"] == "dropped"
    assert status["cell_types[target].integrity"] == "exact"
    back, _ = import_project(tmp_path / "project")
    eff, tgt = back.cell_type("effector"), back.cell_type("target")
    assert eff.interactions.attack_duration == 1e9 and eff.interactions.damage_rate == 2.0
    assert tgt.integrity.damage_repair_rate == 0.1
