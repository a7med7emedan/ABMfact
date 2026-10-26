"""Centre-based mechanics with PhysiCell's potentials, in 2D.

Cells are discs moving by overdamped dynamics: velocity is the sum of pairwise forces plus a motility
term, and positions advance by second-order Adams-Bashforth, ``x += dt (1.5 v - 0.5 v_prev)``. For
cells ``i, j`` at distance ``d`` the pair speed along the unit vector from ``j`` to ``i`` is::

      c_r (1 - d/R)^2  for d < R          (repulsion, R = r_i + r_j, c_r = sqrt(c_r,i c_r,j))
    - c_a (1 - d/S)^2  for d < S          (adhesion, S = s_i r_i + s_j r_j, c_a = sqrt(a_i a_j))

where ``s`` is the relative maximum adhesion distance and ``a_i`` the adhesion strength times the
affinity for the other cell's type. Simple pressure is ``sum (1 - d/R)^2 / 0.027288820670331``. These
follow ``Cell::add_potentials`` and ``Cell::update_position`` in ``core/PhysiCell_cell.cpp`` of
PhysiCell 1.14.2.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

#: PhysiCell's normalisation, ``12 (1 - sqrt(pi / (2 sqrt 3)))^2``: pressure 1 is a hexagonally packed cell.
SIMPLE_PRESSURE_SCALE = 0.027288820670331


@dataclass
class Domain:
    """A rectangular 2D domain, closed or periodic.

    Attributes:
        lo: The lower corner, ``(x, y)``.
        hi: The upper corner, ``(x, y)``.
        periodic: Whether positions wrap around; otherwise they are held at the edge.
    """

    lo: np.ndarray
    hi: np.ndarray
    periodic: bool

    @property
    def size(self) -> np.ndarray:
        """The side lengths, ``hi - lo``."""
        return self.hi - self.lo

    def wrap(self, pos: np.ndarray) -> np.ndarray:
        """Map positions back into the domain: wrap when periodic, else hold them at the edge."""
        if self.periodic:
            return self.lo + np.mod(pos - self.lo, self.size)
        return np.clip(pos, self.lo, self.hi)

    def displacement(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """``a - b`` with the minimum-image convention on a periodic domain."""
        d = a - b
        if self.periodic:
            d -= self.size * np.round(d / self.size)
        return d


def pairs_within(pos: np.ndarray, cutoff: float, domain: Domain) -> np.ndarray:
    """All pairs ``(i, j)``, ``i < j``, closer than ``cutoff``, sorted, as an ``(m, 2)`` array."""
    if len(pos) < 2:
        return np.zeros((0, 2), dtype=np.int64)
    pts = np.mod(pos - domain.lo, domain.size) if domain.periodic else pos
    tree = cKDTree(pts, boxsize=domain.size if domain.periodic else None)
    p = tree.query_pairs(cutoff, output_type="ndarray").astype(np.int64)
    if p.size == 0:
        return np.zeros((0, 2), dtype=np.int64)
    p.sort(axis=1)
    return p[np.lexsort((p[:, 1], p[:, 0]))]


@dataclass
class PairForces:
    """What :func:`pair_forces` returns.

    Attributes:
        velocity: The ``(n, 2)`` velocity of each cell from the pair potentials.
        pressure: The simple pressure on each cell.
        neighbours: The ``(m, 2)`` pairs within the adhesion distance.
    """

    velocity: np.ndarray
    pressure: np.ndarray
    neighbours: np.ndarray  # (m, 2) pairs within the adhesion distance


def pair_forces(
    pos: np.ndarray,
    radius: np.ndarray,
    rep: np.ndarray,
    adh_strength: np.ndarray,
    rel_adh_dist: np.ndarray,
    affinity: np.ndarray,
    types: np.ndarray,
    pairs: np.ndarray,
    domain: Domain,
) -> PairForces:
    """Velocities from pair potentials, simple pressure, and the pairs in contact.

    ``affinity[t_i, t_j]`` is cell type ``t_i``'s adhesive affinity for type ``t_j``.
    """
    n = len(pos)
    vel = np.zeros((n, 2))
    pressure = np.zeros(n)
    if len(pairs) == 0:
        return PairForces(vel, pressure, pairs)
    i, j = pairs[:, 0], pairs[:, 1]
    disp = domain.displacement(pos[i], pos[j])
    d = np.maximum(np.sqrt((disp**2).sum(axis=1)), 1e-5)
    R = radius[i] + radius[j]
    overlap = np.where(d < R, (1.0 - d / R) ** 2, 0.0)
    S = rel_adh_dist[i] * radius[i] + rel_adh_dist[j] * radius[j]
    near = d < S
    a_i = adh_strength[i] * affinity[types[i], types[j]]
    a_j = adh_strength[j] * affinity[types[j], types[i]]
    attract = np.where(near, (1.0 - d / S) ** 2 * np.sqrt(a_i * a_j), 0.0)
    speed = overlap * np.sqrt(rep[i] * rep[j]) - attract
    f = disp * (speed / d)[:, None]
    for k in range(2):
        vel[:, k] += np.bincount(i, weights=f[:, k], minlength=n) - np.bincount(j, weights=f[:, k], minlength=n)
    sp = overlap / SIMPLE_PRESSURE_SCALE
    pressure += np.bincount(i, weights=sp, minlength=n) + np.bincount(j, weights=sp, minlength=n)
    return PairForces(vel, pressure, pairs[near])
