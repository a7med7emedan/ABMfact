"""Diffusion of a substrate on a regular 2D grid.

The PDE is ``dc/dt = D (c_xx + c_yy) - lambda(x) c + g(x)``, where ``lambda`` is decay plus cell uptake
and ``g`` is cell secretion. Each side of the domain is no-flux, held at a value (Dirichlet), or periodic.

**Default scheme: symmetrised backward-Euler locally one-dimensional (LOD) splitting.** A sweep along
x solves ``(I + dt lambda/2 - dt D d_xx) c* = c + dt g/2`` with the standard three-point stencil, and a
sweep along y does the same with ``d_yy``. One step of length ``dt`` takes both orders, x then y and y
then x, and averages them, so neither axis is privileged: rotating the world by 90 degrees gives the same
result, which a fixed sweep order would not (its splitting error depends on the order). Reaction terms sit
inside the implicit solves, so uptake competes with diffusion within each step instead of being capped at
one step's worth of substrate. Each line is a tridiagonal system; all lines of a sweep are solved
together in one LAPACK banded solve, and periodic lines (cyclic systems) use the Sherman-Morrison
correction. The scheme is first order in time and second order in space.

*Stability:* unconditionally stable for every ``dt > 0``. Each sweep's matrix is a strictly diagonally
dominant M-matrix (diagonal ``1 + 2r + dt lambda/2`` against off-diagonals ``-r``, ``r = D dt / h^2``),
so it never amplifies an error mode and it keeps concentrations non-negative; the average of the two
orders is a convex combination and keeps both properties. Large ``dt`` costs accuracy, not stability:
the splitting makes even the steady state first order in ``dt``. Dirichlet sides are the outer layer of
voxels, held at their value (BioFVM's convention).

**Explicit scheme (FTCS), for verification only.** :func:`ftcs_step` uses forward Euler with the same
stencil. *Stability condition:* ``D dt (1/dx^2 + 1/dy^2) <= 1/2``, which for ``dx = dy`` is
``D dt / dx^2 <= 1/4``. Beyond it the checkerboard mode is multiplied by
``1 - 4 D dt (1/dx^2 + 1/dy^2) < -1`` every step and the solution blows up. :func:`ftcs_stable_substeps`
returns the number of equal substeps that satisfies the condition.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.linalg import solve_banded

SIDES = ("x_min", "x_max", "y_min", "y_max")


@dataclass(frozen=True)
class Boundary:
    """Per-side condition: a float holds that side at the value, ``None`` is no-flux.

    ``periodic`` overrides both: the grid wraps in x and y.
    """

    x_min: float | None = None
    x_max: float | None = None
    y_min: float | None = None
    y_max: float | None = None
    periodic: bool = False

    def mask_and_values(self, shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
        """Boolean mask of Dirichlet voxels and the value each one is held at (later sides win at corners)."""
        mask = np.zeros(shape, dtype=bool)
        values = np.zeros(shape)
        if self.periodic:
            return mask, values
        for side, sl in (
            ("y_min", np.s_[0, :]),
            ("y_max", np.s_[-1, :]),
            ("x_min", np.s_[:, 0]),
            ("x_max", np.s_[:, -1]),
        ):
            v = getattr(self, side)
            if v is not None:
                mask[sl] = True
                values[sl] = v
        return mask, values


def tridiagonal_lines(sub: np.ndarray, diag: np.ndarray, sup: np.ndarray, rhs: np.ndarray) -> np.ndarray:
    """Solve independent tridiagonal systems along axis 0, one per column, in a single banded solve.

    The lines are stacked end to end into one banded system with zero coupling between lines and solved
    by LAPACK through :func:`scipy.linalg.solve_banded`. ``sub[0]`` and ``sup[-1]`` are ignored. ``rhs``
    may carry extra trailing axes for several right-hand sides.
    """
    n, m = diag.shape
    sub = sub.copy()
    sup = sup.copy()
    sub[0] = 0.0
    sup[-1] = 0.0
    ab = np.empty((3, n * m))
    ab[0] = np.roll(sup.T.ravel(), 1)
    ab[1] = diag.T.ravel()
    ab[2] = np.roll(sub.T.ravel(), -1)
    b = np.moveaxis(rhs, 0, 1).reshape(n * m, *rhs.shape[2:])
    x = solve_banded((1, 1), ab, b, overwrite_ab=True, overwrite_b=True, check_finite=False)
    return np.moveaxis(x.reshape(m, n, *rhs.shape[2:]), 1, 0)


def cyclic_lines(off: np.ndarray, diag: np.ndarray, rhs: np.ndarray) -> np.ndarray:
    """Periodic tridiagonal systems along axis 0 (constant off-diagonal, including the two corners),
    solved by the Sherman-Morrison correction to a non-periodic system."""
    corner = off[0]
    gamma = -diag[0]
    bb = diag.copy()
    bb[0] = diag[0] - gamma
    bb[-1] = diag[-1] - corner * corner / gamma
    u = np.zeros_like(rhs)
    u[0] = gamma
    u[-1] = corner
    both = tridiagonal_lines(off, bb, off, np.stack([rhs, u], axis=-1))
    x, z = both[..., 0], both[..., 1]
    fact = (x[0] + corner * x[-1] / gamma) / (1.0 + z[0] + corner * z[-1] / gamma)
    return x - fact * z


def _sweep(
    c: np.ndarray, r: float, react: np.ndarray, axis: int, periodic: bool, mask: np.ndarray, values: np.ndarray
) -> np.ndarray:
    """Solve ``(I + react - r * second difference along axis) c' = c`` with the boundary conditions."""
    rhs = np.moveaxis(c, axis, 0).astype(float, copy=True)
    k = np.moveaxis(np.broadcast_to(react, c.shape), axis, 0)
    held = np.moveaxis(mask, axis, 0)
    n = rhs.shape[0]
    if n == 1:
        return np.moveaxis(np.where(held, np.moveaxis(values, axis, 0), rhs / (1.0 + k)), 0, axis)
    off = np.full(rhs.shape, -r)
    diag = 1.0 + 2.0 * r + k
    if periodic and n >= 3:
        return np.moveaxis(cyclic_lines(off, diag, rhs), 0, axis)
    diag[0] -= r
    diag[-1] -= r
    sub, sup = off.copy(), off.copy()
    if held.any():
        diag = np.where(held, 1.0, diag)
        sub = np.where(held, 0.0, sub)
        sup = np.where(held, 0.0, sup)
        rhs = np.where(held, np.moveaxis(values, axis, 0), rhs)
    return np.moveaxis(tridiagonal_lines(sub, diag, sup, rhs), 0, axis)


def implicit_step(
    c: np.ndarray,
    D: float,
    decay: float,
    dt: float,
    dx: float,
    dy: float,
    boundary: Boundary,
    gain: np.ndarray | None = None,
    loss: np.ndarray | None = None,
) -> np.ndarray:
    """One symmetrised backward-Euler LOD step with reactions inside the sweeps (see the module docstring).

    ``gain`` (amount per time) and ``loss`` (rate, per time) are per-voxel cell terms; ``decay`` is the
    substrate's own decay rate.
    """
    c = np.asarray(c, dtype=float)
    lam = np.broadcast_to(decay + (loss if loss is not None else 0.0), c.shape)
    half_gain = 0.5 * dt * (gain if gain is not None else 0.0)
    react = 0.5 * dt * lam
    mask, values = boundary.mask_and_values(c.shape)
    rx, ry = D * dt / (dx * dx), D * dt / (dy * dy)

    def sweep_x(u: np.ndarray) -> np.ndarray:
        return _sweep(u + half_gain, rx, react, 1, boundary.periodic, mask, values)

    def sweep_y(u: np.ndarray) -> np.ndarray:
        return _sweep(u + half_gain, ry, react, 0, boundary.periodic, mask, values)

    c = 0.5 * (sweep_y(sweep_x(c)) + sweep_x(sweep_y(c)))
    return np.where(mask, values, c) if mask.any() else c


def laplacian(c: np.ndarray, dx: float, dy: float, periodic: bool) -> np.ndarray:
    """Five-point Laplacian with mirror (no-flux) or wrapped edges."""
    p = np.pad(c, 1, mode="wrap") if periodic else np.pad(c, 1, mode="edge")
    return (p[1:-1, 2:] - 2 * c + p[1:-1, :-2]) / (dx * dx) + (p[2:, 1:-1] - 2 * c + p[:-2, 1:-1]) / (dy * dy)


def ftcs_step(c: np.ndarray, D: float, decay: float, dt: float, dx: float, dy: float, boundary: Boundary) -> np.ndarray:
    """One explicit forward-Euler step. Stable only when ``D dt (1/dx^2 + 1/dy^2) <= 1/2``."""
    c = np.asarray(c, dtype=float)
    out = c + dt * (D * laplacian(c, dx, dy, boundary.periodic) - decay * c)
    mask, values = boundary.mask_and_values(c.shape)
    return np.where(mask, values, out) if mask.any() else out


def ftcs_number(D: float, dt: float, dx: float, dy: float) -> float:
    """``D dt (1/dx^2 + 1/dy^2)``; the explicit scheme is stable when this is at most 1/2."""
    return D * dt * (1.0 / (dx * dx) + 1.0 / (dy * dy))


def ftcs_stable_substeps(D: float, dt: float, dx: float, dy: float | None = None) -> int:
    """The fewest equal substeps of ``dt`` that keep the explicit scheme within its stability limit."""
    number = ftcs_number(D, dt, dx, dx if dy is None else dy)
    return max(1, math.ceil(number / 0.5 - 1e-12))
