"""The reference engine's diffusion solver and mechanics against known answers."""

from __future__ import annotations

import math

import numpy as np
import pytest

from abmfact.reference.diffusion import (
    Boundary,
    cyclic_lines,
    ftcs_number,
    ftcs_stable_substeps,
    ftcs_step,
    implicit_step,
    laplacian,
)
from abmfact.reference.mechanics import SIMPLE_PRESSURE_SCALE, Domain, pair_forces, pairs_within


def _gaussian_error(h: float, solver: str) -> float:
    """Diffuse a Gaussian on a periodic square and compare with the exact heat kernel."""
    L, D, t_end = 200.0, 10.0, 20.0
    n = round(L / h)
    x = (np.arange(n) + 0.5) * h - L / 2
    X, Y = np.meshgrid(x, x)
    s0 = 15.0
    c = np.exp(-(X**2 + Y**2) / (2 * s0**2))
    dt = 0.002 * h * h if solver == "explicit" else 0.05 * (h / 2.0) ** 2
    steps = round(t_end / dt)
    bc = Boundary(periodic=True)
    for _ in range(steps):
        c = ftcs_step(c, D, 0.0, dt, h, h, bc) if solver == "explicit" else implicit_step(c, D, 0.0, dt, h, h, bc)
    s2 = s0**2 + 2 * D * steps * dt
    exact = (s0**2 / s2) * np.exp(-(X**2 + Y**2) / (2 * s2))
    return float(np.abs(c - exact).max())


@pytest.mark.parametrize("solver", ["explicit", "implicit"])
def test_second_order_in_space(solver):
    e_coarse, e_fine = _gaussian_error(4.0, solver), _gaussian_error(2.0, solver)
    order = math.log2(e_coarse / e_fine)
    assert 1.7 < order < 2.3, order


def test_implicit_steady_state_is_first_order_in_dt():
    nx, dx, D, lam = 50, 2.0, 1.0, 0.01
    A = np.zeros((nx, nx))
    rhs = np.zeros(nx)
    A[0, 0] = A[-1, -1] = 1.0
    rhs[0] = rhs[-1] = 1.0
    for i in range(1, nx - 1):
        A[i, i - 1] = A[i, i + 1] = -D / dx**2
        A[i, i] = 2 * D / dx**2 + lam
    exact = np.linalg.solve(A, rhs)
    errors = []
    for dt in (8.0, 4.0):
        c = np.zeros((3, nx))
        for _ in range(int(16000 / dt)):
            c = implicit_step(c, D, lam, dt, dx, dx, Boundary(x_min=1.0, x_max=1.0))
        errors.append(np.abs(c[1] - exact).max())
    assert 1.8 < errors[0] / errors[1] < 2.2


def test_mass_conservation_positivity_and_dirichlet():
    rng = np.random.default_rng(0)
    c = rng.random((20, 30))
    for bc in (Boundary(periodic=True), Boundary()):
        out = implicit_step(c, 50.0, 0.0, 10.0, 1.0, 1.0, bc)
        assert out.sum() == pytest.approx(c.sum(), rel=1e-12) and out.min() > 0
    held = implicit_step(c, 50.0, 0.0, 10.0, 1.0, 1.0, Boundary(x_min=2.0, y_max=3.0))
    assert np.all(held[:, 0] == 2.0) and np.all(held[-1, 1:] == 3.0)  # x sides win at corners
    sink = implicit_step(np.ones((5, 5)), 1.0, 0.0, 1.0, 1.0, 1.0, Boundary(), loss=np.full((5, 5), 1e6))
    assert 0 <= sink.max() < 1e-5
    source = implicit_step(np.zeros((1, 1)), 1.0, 0.0, 2.0, 1.0, 1.0, Boundary(), gain=np.ones((1, 1)))
    assert source[0, 0] == pytest.approx(2.0 / (1.0 + 0.0))


def test_cyclic_solver_matches_dense():
    from scipy.linalg import circulant

    n, r = 7, 0.8
    col = np.zeros(n)
    col[[0, 1, -1]] = [1 + 2 * r, -r, -r]
    d = np.random.default_rng(1).random((n, 3))
    got = cyclic_lines(np.full((n, 3), -r), np.full((n, 3), 1 + 2 * r), d)
    np.testing.assert_allclose(got, np.linalg.solve(circulant(col), d), atol=1e-14)


def test_ftcs_stability_limit():
    D, dx = 1.0, 1.0
    dt_ok, dt_bad = 0.25, 0.3
    assert ftcs_number(D, dt_ok, dx, dx) == 0.5 and ftcs_stable_substeps(D, dt_ok, dx) == 1
    assert ftcs_stable_substeps(D, dt_bad, dx) == 2 and ftcs_stable_substeps(D, 1.0, dx, 0.5) == 10
    checker = np.indices((16, 16)).sum(axis=0) % 2 * 2.0 - 1.0
    bc = Boundary(periodic=True)
    stable, unstable = checker.copy(), checker.copy()
    for _ in range(50):
        stable = ftcs_step(stable, D, 0.0, dt_ok, dx, dx, bc)
        unstable = ftcs_step(unstable, D, 0.0, dt_bad, dx, dx, bc)
    assert np.abs(stable).max() <= 1.0 + 1e-12
    assert np.abs(unstable).max() > 1e3
    assert laplacian(np.ones((4, 4)), 1.0, 1.0, periodic=False).max() == 0.0


def test_two_cells_settle_where_repulsion_balances_adhesion():
    dom = Domain(np.array([-50.0, -50.0]), np.array([50.0, 50.0]), periodic=False)
    pos = np.array([[-3.0, 0.0], [3.0, 0.0]])
    r = np.array([5.0, 5.0])
    ones = np.ones(2)
    for _ in range(4000):
        pairs = pairs_within(pos, 15.0, dom)
        pf = pair_forces(pos, r, 10 * ones, 0.4 * ones, 1.25 * ones, np.ones((1, 1)), np.zeros(2, int), pairs, dom)
        pos = pos + 0.01 * pf.velocity
    d = np.linalg.norm(pos[0] - pos[1])
    # Balance: 10 (1 - d/10)^2 = 0.4 (1 - d/12.5)^2
    k = math.sqrt(0.4 / 10)
    assert d == pytest.approx((1 - k) / (1 / 10 - k / 12.5), rel=1e-3)
    assert pf.pressure[0] == pytest.approx((1 - d / 10) ** 2 / SIMPLE_PRESSURE_SCALE, rel=1e-3)


def test_periodic_pairs_use_the_minimum_image():
    dom = Domain(np.array([0.0, 0.0]), np.array([100.0, 100.0]), periodic=True)
    pos = np.array([[1.0, 50.0], [99.0, 50.0], [50.0, 50.0]])
    pairs = pairs_within(pos, 5.0, dom)
    assert pairs.tolist() == [[0, 1]]
    assert dom.displacement(pos[0], pos[1]).tolist() == [2.0, 0.0]
    assert dom.wrap(np.array([[101.0, -1.0]])).tolist() == [[1.0, 99.0]]
    closed = Domain(np.array([0.0, 0.0]), np.array([10.0, 10.0]), periodic=False)
    assert closed.wrap(np.array([[11.0, -1.0]])).tolist() == [[10.0, 0.0]]
    assert pairs_within(pos[:1], 5.0, dom).shape == (0, 2)
