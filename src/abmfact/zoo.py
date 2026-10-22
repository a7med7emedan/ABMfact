"""Planted-fault zoo: one clean reference model and one model per check, each with a single known bug.

The reference model is an SIR epidemic on an ``L x L`` torus with one agent per cell. A fraction
``immune`` of agents, drawn at random, starts recovered. Patient zero is a plus of five agents at
``(x0, y0)``. Each infected von Neumann neighbour adds ``beta`` to a susceptible agent's hazard, every
infected agent anywhere adds ``beta_g / N`` (weak global mixing), and infected agents recover at rate
``gamma``. Transition probabilities are ``1 - exp(-rate * dt)``, and every update is computed from a
snapshot of the state at the start of the step. Activation order therefore only decides which random
number each agent receives, so a correct implementation gives the same distribution of results under
every order. The grid knobs move the whole world (agents, ids and initial state) relative to the array.

Each ``fault_*`` model differs from the clean model in exactly one place, marked ``# FAULT``. A second
clean model with a diffusing nutrient (``field_clean``) carries one more fault, ``unstable_diffusion``,
that only the convergence check can catch. The acceptance test checks the exact expected detection
matrix: each fault caught by its own check and by no other, except one documented overlap
(``EXPECTED_ALSO``): ``dt_unscaled`` is also caught by the convergence check, because both refine ``dt``.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

import numpy as np

from .adapters import PythonAdapter
from .knobs import RNG_SALT, Knobs

S, I, R = 0, 1, 2

DEFAULT_PARAMS: dict[str, Any] = {
    "beta": 0.4,
    "beta_g": 0.03,
    "gamma": 0.1,
    "immune": 0.2,
    "L": 20,
    "T": 60,
    "dt": 0.25,
    "x0": 0.4,
    "y0": 0.4,
}

FAULTS = (
    "nondeterminism",
    "stale_state",
    "id_dependence",
    "ignored_seed",
    "boundary",
    "dt_unscaled",
    "density",
)

#: Which check is expected to catch each fault.
EXPECTED_CHECK = {
    "nondeterminism": "determinism",
    "stale_state": "update_order",
    "id_dependence": "agent_order",
    "ignored_seed": "rng_streams",
    "boundary": "grid",
    "dt_unscaled": "time_step",
    "density": "population_scale",
}

SUPPORT: dict[str, Any] = {
    "update_order": {"values": ["random", "id", "reverse", "synchronous"], "default": "random"},
    "agent_order": True,
    "rng_streams": True,
    "grid": True,
    "dt_scale": True,
    "pop_scale": True,
}


def _neighbour_index(L: int, wrap: bool) -> np.ndarray:
    """Indices of the four von Neumann neighbours of every cell. Index ``N`` is a padding zero."""
    y, x = np.divmod(np.arange(L * L), L)
    cols = []
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, ny = x + dx, y + dy
        if wrap:
            cols.append((ny % L) * L + nx % L)
        else:
            # FAULT (boundary) when wrap is False: zero padding instead of wrapping, so the torus is
            # really a box with walls.
            inside = (nx >= 0) & (nx < L) & (ny >= 0) & (ny < L)
            cols.append(np.where(inside, ny * L + nx, L * L))
    return np.stack(cols, axis=1)


def _world_to_array(L: int, offset: tuple[float, float], rotation: int) -> np.ndarray:
    """Array cell index for every world cell, in world row-major order.

    The grid knobs move the whole world (agents, their ids and the initial state) relative to the
    array that stores it. On a correct torus nothing observable changes.
    """
    ys, xs = np.divmod(np.arange(L * L), L)
    x = xs.astype(np.int64)
    y = ys.astype(np.int64)
    for _ in range((rotation // 90) % 4):
        x, y = (L - 1) - y, x
    ox, oy = round(offset[0] * L), round(offset[1] * L)
    return ((y + oy) % L) * L + (x + ox) % L


def simulate(params: Mapping[str, Any], seed: int, knobs: Knobs, fault: str | None = None) -> dict[str, Any]:
    """Run the reference model, optionally with one planted fault."""
    if fault is not None and fault not in FAULTS:
        raise ValueError(f"unknown fault {fault!r}; expected one of {FAULTS}")
    p = {**DEFAULT_PARAMS, **params}
    beta, beta_g, gamma = float(p["beta"]), float(p["beta_g"]), float(p["gamma"])
    immune_frac = float(p["immune"])
    L0 = int(p["L"])
    L = max(3, round(L0 * math.sqrt(knobs.pop_scale)))
    N = L * L
    dt = float(p["dt"]) * knobs.dt_scale
    steps_per_unit = max(1, round(1.0 / dt))
    n_steps = round(float(p["T"]) / dt)

    if fault == "ignored_seed":
        seed = 12345  # FAULT (ignored_seed): the replicate seed never reaches the generator.

    if knobs.rng_streams == "independent":
        ss = np.random.SeedSequence([int(seed) & 0xFFFF_FFFF, RNG_SALT])
    else:
        ss = np.random.SeedSequence(int(seed) & 0xFFFF_FFFF)
    rng_init, rng_order, rng_tx, rng_rec = (np.random.default_rng(s) for s in ss.spawn(4))

    # Agents are created in world row-major order; the grid knobs place the world in the array.
    world2cell = _world_to_array(L, knobs.grid_offset, knobs.grid_rotation)
    if knobs.agent_order == "shuffled":
        prng = np.random.default_rng(knobs.agent_perm_seed if knobs.agent_perm_seed is not None else 0)
        id2cell = world2cell[prng.permutation(N)]
    else:
        id2cell = world2cell

    state = np.zeros(N, dtype=np.int8)
    if fault == "density":
        # FAULT (density): the immune count is computed from the calibration size, not the current one.
        n_immune = round(immune_frac * L0 * L0)
    else:
        n_immune = round(immune_frac * N)
    if fault == "id_dependence":
        immune_ids = np.arange(n_immune)  # FAULT (id_dependence): the first agents created, not a sample.
    else:
        immune_ids = rng_init.choice(N, size=n_immune, replace=False)
    state[id2cell[immune_ids]] = R

    # Patient zero: a plus of five agents centred on (x0, y0) in world coordinates.
    cx, cy = int(float(p["x0"]) * L) % L, int(float(p["y0"]) * L) % L
    for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
        state[world2cell[((cy + dy) % L) * L + (cx + dx) % L]] = I

    order_mode = knobs.update_order or "random"
    if order_mode not in ("random", "id", "reverse", "synchronous"):
        raise ValueError(f"unsupported update_order {order_mode!r}")
    nbr = _neighbour_index(L, wrap=fault != "boundary")
    p_rec = 1.0 - math.exp(-gamma * dt)
    dt_tx = float(p["dt"]) if fault == "dt_unscaled" else dt
    # FAULT (dt_unscaled) when fault == "dt_unscaled": the rate conversion uses the calibration step.

    n_units = n_steps // steps_per_unit
    prevalence: list[float] = []
    ever = state == I
    inf_pad = np.zeros(N + 1, dtype=np.int32)
    cell_u_tx = np.empty(N)
    cell_u_rec = np.empty(N)
    stale_buffer = state.copy() if fault == "stale_state" else None
    ids = np.arange(N)
    for step in range(n_steps):
        inf_now = state == I
        if not inf_now.any() and (stale_buffer is None or not (stale_buffer == I).any()):
            prevalence.extend([0.0] * (n_units - len(prevalence)))
            break
        if order_mode in ("random", "synchronous"):
            if fault == "nondeterminism":
                order = list(range(N))
                random.shuffle(order)  # FAULT (nondeterminism): unseeded global generator.
                order_arr = np.asarray(order)
            else:
                order_arr = rng_order.permutation(N)
        elif order_mode == "id":
            order_arr = ids
        else:
            order_arr = ids[::-1]
        # Agent at activation position j receives the j-th draw of each stream.
        cells_in_order = id2cell[order_arr]
        cell_u_tx[cells_in_order] = rng_tx.random(N)
        cell_u_rec[cells_in_order] = rng_rec.random(N)

        inf_pad[:N] = inf_now
        k = inf_pad[nbr].sum(axis=1)
        hazard = beta * k + beta_g * float(inf_pad[:N].sum()) / N
        p_inf = 1.0 - np.exp(-hazard * dt_tx)
        new_inf = (state == S) & (cell_u_tx < p_inf)
        new_rec = inf_now & (cell_u_rec < p_rec)
        if stale_buffer is not None and order_mode == "synchronous":
            # FAULT (stale_state): the synchronous path writes only the agents that changed into a
            # buffer that is swapped every step, so unchanged agents read their state from two steps ago.
            stale_buffer[new_inf] = I
            stale_buffer[new_rec] = R
            stale_buffer, state = state, stale_buffer
        else:
            state[new_inf] = I
            state[new_rec] = R
        ever |= state == I
        if (step + 1) % steps_per_unit == 0:
            prevalence.append(float((state == I).mean()))

    prev = np.asarray(prevalence) if prevalence else np.zeros(1)
    peak_idx = int(np.argmax(prev))
    return {
        "prevalence": prev.tolist(),
        "attack_rate": float(ever.mean()),
        "peak_prevalence": float(np.max(prev)),
        "peak_time": float(peak_idx + 1),
    }


class ZooRunner:
    """A picklable ``run(params, seed, knobs)`` for one zoo model, so audits can run in parallel."""

    def __init__(self, fault: str | None = None):
        if fault is not None and fault not in FAULTS:
            raise ValueError(f"unknown fault {fault!r}")
        self.fault = fault

    def __call__(self, params: Mapping[str, Any], seed: int, knobs: Knobs) -> dict[str, Any]:
        return simulate(params, seed, knobs, fault=self.fault)

    def __repr__(self) -> str:
        return f"ZooRunner({self.fault!r})"


def _adapter(fault: str | None) -> PythonAdapter:
    return PythonAdapter(ZooRunner(fault), supports=SUPPORT, name=f"zoo:{fault or 'clean'}")


clean = _adapter(None)
fault_nondeterminism = _adapter("nondeterminism")
fault_stale_state = _adapter("stale_state")
fault_id_dependence = _adapter("id_dependence")
fault_ignored_seed = _adapter("ignored_seed")
fault_boundary = _adapter("boundary")
fault_dt_unscaled = _adapter("dt_unscaled")
fault_density = _adapter("density")

MODELS: dict[str, PythonAdapter] = {
    "clean": clean,
    "nondeterminism": fault_nondeterminism,
    "stale_state": fault_stale_state,
    "id_dependence": fault_id_dependence,
    "ignored_seed": fault_ignored_seed,
    "boundary": fault_boundary,
    "dt_unscaled": fault_dt_unscaled,
    "density": fault_density,
}


#: Claims used to validate the checks. They are written the way a paper would state them.
CLAIMS: list[dict[str, Any]] = [
    {
        "id": "C1",
        "statement": "most of the population is infected",
        "metric": "attack_rate",
        "test": "greater_than",
        "value": 0.6,
    },
    {
        "id": "C2",
        "statement": "the attack rate rises with local transmission",
        "metric": "attack_rate",
        "test": "monotone_increasing",
        "over": "beta",
        "where": {"beta": [0.25, 0.4]},
    },
    {
        "id": "C3",
        "statement": "peak prevalence stays below half the population",
        "metric": "peak_prevalence",
        "test": "less_than",
        "value": 0.5,
    },
    {
        "id": "C4",
        "statement": "the epidemic peaks within 25 time units",
        "metric": "peak_time",
        "test": "less_than",
        "value": 25,
        "skip_checks": {"population_scale": "peak time legitimately grows with system size"},
    },
]


def audit_document(model: str = "clean", seeds: int = 100, jobs: int = 1) -> dict[str, Any]:
    """The audit file for one zoo model, as a mapping (the same shape as an audit YAML file)."""
    if model not in MODELS:
        raise ValueError(f"unknown zoo model {model!r}; expected one of {sorted(MODELS)}")
    if model in FIELD_MODELS:
        return {
            "title": f"Planted-fault zoo: {model}",
            "model": {
                "adapter": "python",
                "target": f"abmfact.zoo:{'field_clean' if model == 'field_clean' else 'fault_' + model}",
            },
            "params": dict(FIELD_PARAMS),
            "claims": [dict(c) for c in FIELD_CLAIMS],
            "budget": {"seeds": seeds, "jobs": jobs},
        }
    attr = "clean" if model == "clean" else f"fault_{model}"
    return {
        "title": f"Planted-fault zoo: {model}",
        "model": {"adapter": "python", "target": f"abmfact.zoo:{attr}"},
        "params": dict(DEFAULT_PARAMS),
        "claims": [dict(c) for c in CLAIMS],
        "budget": {"seeds": seeds, "jobs": jobs},
    }


# ---------------------------------------------------------------------------------------------------------
# The field model: a planted fault that only the convergence check can catch.
#
# Cells sit in a square domain and divide at a rate set by a nutrient that diffuses in from the edges
# (held at 1) and decays inside. The nutrient is solved with the explicit FTCS scheme on a grid of
# spacing ``dx``, in steps of ``dt``, with the edge value imposed on the faces (second order in space).
# The cells update every minute, reading the nutrient at their position by bilinear interpolation. The
# clean model splits each step into the substeps the FTCS stability limit needs. At the default
# resolution, and when only ``dt`` is halved, one step is enough, so the fault model below is identical
# to the clean one there. It only differs when ``dx`` shrinks too.

FIELD_FAULTS = ("unstable_diffusion",)

FIELD_PARAMS: dict[str, Any] = {
    "L": 200.0,  # domain side (micron)
    "D": 100.0,  # nutrient diffusion coefficient (micron^2/min)
    "decay": 0.0625,  # nutrient decay rate (1/min): penetration length sqrt(D/decay) = 40 micron
    "dx": 10.0,  # nutrient grid spacing (micron)
    "dt": 0.2,  # nutrient time step (min): D dt (2/dx^2) = 0.4, inside the FTCS limit of 0.5
    "T": 600.0,  # simulated time (min)
    "n0": 50,  # initial cells
    "r": 0.01,  # division rate at saturating nutrient (1/min)
    "K": 0.5,  # nutrient level for half the maximal division rate
    "d": 0.002,  # death rate (1/min)
}

FIELD_SUPPORT: dict[str, Any] = {
    "agent_order": True,
    "rng_streams": True,
    "dt_scale": True,
    "dx_scale": True,
    # Not scale-invariant (nutrient comes from the edges), not a torus, and no activation order.
}


def _ftcs_face_dirichlet(c: np.ndarray, D: float, decay: float, dt: float, h: float, edge: float) -> np.ndarray:
    """One explicit step with the edge value imposed on the domain faces through ghost cells (second order).

    Stable when ``D dt (2 / h^2) <= 1/2``, the same limit as with no-flux edges (Gershgorin).
    """
    p = np.empty((c.shape[0] + 2, c.shape[1] + 2))
    p[1:-1, 1:-1] = c
    p[0, 1:-1] = 2 * edge - c[0]
    p[-1, 1:-1] = 2 * edge - c[-1]
    p[1:-1, 0] = 2 * edge - c[:, 0]
    p[1:-1, -1] = 2 * edge - c[:, -1]
    lap = (p[1:-1, 2:] + p[1:-1, :-2] + p[2:, 1:-1] + p[:-2, 1:-1] - 4 * c) / (h * h)
    return c + dt * (D * lap - decay * c)


@lru_cache(maxsize=8)
def _nutrient(L: float, D: float, decay: float, dx: float, dt: float, T: float, fault: str | None) -> np.ndarray:
    """The nutrient field once per simulated minute, shape ``(minutes + 1, n, n)``. It does not depend on
    the cells, so it is computed once per resolution and shared by every seed."""
    from .reference.diffusion import ftcs_stable_substeps

    n = max(3, round(L / dx))
    h = L / n
    c = np.ones((n, n))
    steps_per_min = max(1, round(1.0 / dt))
    step = 1.0 / steps_per_min
    if fault == "unstable_diffusion":
        substeps = 1  # FAULT (unstable_diffusion): one explicit step whatever the stability limit says.
    else:
        substeps = ftcs_stable_substeps(D, step, h)
    out = np.empty((round(T) + 1, n, n))
    out[0] = c
    with np.errstate(over="ignore", invalid="ignore"):
        for minute in range(1, round(T) + 1):
            for _ in range(steps_per_min * substeps):
                c = _ftcs_face_dirichlet(c, D, decay, step / substeps, h, 1.0)
            out[minute] = c
    return out


def simulate_field(params: Mapping[str, Any], seed: int, knobs: Knobs, fault: str | None = None) -> dict[str, Any]:
    """Run the field model, optionally with the unstable-diffusion fault."""
    from scipy.ndimage import map_coordinates

    if fault is not None and fault not in FIELD_FAULTS:
        raise ValueError(f"unknown field fault {fault!r}; expected one of {FIELD_FAULTS}")
    p = {**FIELD_PARAMS, **params}
    L = float(p["L"])
    field = _nutrient(L, float(p["D"]), float(p["decay"]), float(p["dx"]) * knobs.dx_scale,
                      float(p["dt"]) * knobs.dt_scale, float(p["T"]), fault)  # fmt: skip
    n = field.shape[1]
    h = L / n
    salt = [RNG_SALT] if knobs.rng_streams == "independent" else []
    rng_init, rng_div, rng_death, rng_jitter = (
        np.random.default_rng(s) for s in np.random.SeedSequence([int(seed) & 0xFFFF_FFFF, *salt]).spawn(4)
    )
    n0 = int(p["n0"])
    pos = rng_init.random((n0, 2)) * L
    if knobs.agent_order == "shuffled":
        pos = pos[np.random.default_rng(knobs.agent_perm_seed or 0).permutation(n0)]
    r, K, d = float(p["r"]), float(p["K"]), float(p["d"])
    for minute in range(round(float(p["T"]))):
        # Grid index of each cell: voxel centres sit at (i + 0.5) h.
        coords = np.stack([pos[:, 1] / h - 0.5, pos[:, 0] / h - 0.5])
        with np.errstate(invalid="ignore"):
            c = map_coordinates(field[minute], coords, order=1, mode="nearest")
            rate = r * c / (c + K)
            divide = rng_div.random(len(pos)) < -np.expm1(-rate)
        die = rng_death.random(len(pos)) < -np.expm1(-d)
        jitter = rng_jitter.normal(0.0, 2.0, (int(divide.sum()), 2))
        daughters = np.clip(pos[divide] + jitter, 0.0, L)
        pos = np.concatenate([pos[~die], daughters])
    coords = np.stack([pos[:, 1] / h - 0.5, pos[:, 0] / h - 0.5])
    sensed = map_coordinates(field[-1], coords, order=1, mode="nearest") if len(pos) else np.zeros(0)
    return {
        "growth": len(pos) / n0,
        "sensed": float(sensed.mean()) if len(pos) else 0.0,
        "nutrient_mean": float(field[-1].mean()),
    }


class FieldRunner:
    """A picklable ``run(params, seed, knobs)`` for one field model."""

    def __init__(self, fault: str | None = None):
        if fault is not None and fault not in FIELD_FAULTS:
            raise ValueError(f"unknown field fault {fault!r}")
        self.fault = fault

    def __call__(self, params: Mapping[str, Any], seed: int, knobs: Knobs) -> dict[str, Any]:
        return simulate_field(params, seed, knobs, fault=self.fault)

    def __repr__(self) -> str:
        return f"FieldRunner({self.fault!r})"


field_clean = PythonAdapter(FieldRunner(None), supports=FIELD_SUPPORT, name="zoo:field_clean")
fault_unstable_diffusion = PythonAdapter(
    FieldRunner("unstable_diffusion"), supports=FIELD_SUPPORT, name="zoo:unstable_diffusion"
)

FIELD_CLAIMS: list[dict[str, Any]] = [
    {
        "id": "F1",
        "statement": "the population at least triples in ten hours",
        "metric": "growth",
        "test": "greater_than",
        "value": 3,
    },
    {
        "id": "F2",
        "statement": "cells sense less nutrient than the edges supply",
        "metric": "sensed",
        "test": "less_than",
        "value": 0.8,
    },
]

FIELD_MODELS: dict[str, PythonAdapter] = {"field_clean": field_clean, "unstable_diffusion": fault_unstable_diffusion}

#: Which check must catch the field fault.
EXPECTED_CHECK["unstable_diffusion"] = "convergence"

#: Checks besides the expected one that must also catch a fault, and why. Both the time_step check and
#: the convergence check refine dt, and a rate tied to the calibration step genuinely does not converge.
EXPECTED_ALSO: dict[str, tuple[str, ...]] = {"dt_unscaled": ("convergence",)}

MODELS.update(FIELD_MODELS)
