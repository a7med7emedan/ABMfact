"""Sweep designs: parameters, scales, grids, Latin hypercubes and Sobol points."""

from __future__ import annotations

import numpy as np
import pytest

from abmfact.modes.design import (
    DesignError,
    DesignWarning,
    Parameter,
    grid,
    latin_hypercube,
    make_design,
    setting,
    sobol,
)

A = Parameter("rules[0].half_max", 0.25, 1.0)
B = Parameter("cell_types[x].cycle.rates[0]", 1e-4, 1e-2, scale="log")
C = Parameter("option:pressure_gain", 0.5, 2.0)
D = Parameter("overall/omp_num_threads", 1, 4, integer=True)


def test_parameter_kinds_and_keys():
    assert (A.kind, B.kind, C.kind, D.kind) == ("ir", "ir", "option", "native")
    assert C.key == "pressure_gain" and A.key == A.name


def test_log_scale_spans_orders_of_magnitude_evenly():
    v = B.scaled(np.array([0.0, 0.5, 1.0]))
    assert v == pytest.approx([1e-4, 1e-3, 1e-2])
    assert B.unit(v) == pytest.approx([0.0, 0.5, 1.0])


@pytest.mark.parametrize(
    "bad",
    [dict(low=1, high=1), dict(low=2, high=1), dict(low=0, high=1, scale="log"), dict(low=0, high=1, scale="cubic")],
)
def test_bad_ranges_are_refused(bad):
    with pytest.raises(DesignError):
        Parameter("p", **bad)


def test_grids_take_every_combination_and_one_level_is_the_middle():
    g = grid([A, D], [3, 2])
    assert g.shape == (6, 2)
    assert sorted(set(g[:, 0])) == pytest.approx([0.25, 0.625, 1.0])
    assert set(g[:, 1]) == {1.0, 4.0}
    assert grid([A], 1)[0, 0] == pytest.approx(0.625)


def test_latin_hypercube_puts_one_point_in_each_stratum():
    x = latin_hypercube([A, B], 10, seed=3)
    u = A.unit(x[:, 0])
    assert sorted(np.floor(u * 10).astype(int)) == list(range(10))
    assert np.array_equal(x, latin_hypercube([A, B], 10, seed=3))
    assert not np.array_equal(x, latin_hypercube([A, B], 10, seed=4))


def test_sobol_warns_unless_a_power_of_two():
    with pytest.warns(DesignWarning, match="powers of two"):
        sobol([A, B], 12)
    x = sobol([A, B], 16, seed=1)
    assert x.shape == (16, 2) and (x[:, 0] >= 0.25).all() and (x[:, 0] <= 1.0).all()


def test_make_design_checks_its_inputs():
    with pytest.raises(DesignError, match="levels"):
        make_design([A], "grid")
    with pytest.raises(DesignError, match="needs n"):
        make_design([A], "lhs")
    with pytest.raises(DesignError, match="one of"):
        make_design([A], "random", 4)
    with pytest.raises(DesignError, match="once"):
        make_design([A, A], "lhs", 4)


def test_a_point_splits_into_parameters_and_options():
    params, options = setting([A, C, D], [0.5, 1.5, 3.0], {"experiment.max_time": 60}, {"threads": 1})
    assert params == {"experiment.max_time": 60, "rules[0].half_max": 0.5, "overall/omp_num_threads": 3}
    assert options == {"threads": 1, "pressure_gain": 1.5}
    assert isinstance(params["overall/omp_num_threads"], int)
