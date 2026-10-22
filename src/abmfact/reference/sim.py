"""The reference engine's simulation loop.

Time advances in ticks of the smallest of the three steps (diffusion, mechanics, phenotype); each process
runs when its own step comes round. Within a tick the order is: timed events, diffusion, phenotype
(rules, death, division, removal), mechanics. Every per-cell random number comes from a stream spawned
from the run seed, assigned to cells in activation order, so the same seed always gives the same run
and the activation order changes only which cell receives which number.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field

import numpy as np

from ..ir import IRModel
from ..knobs import RNG_SALT, Knobs
from ..records import APOPTOTIC, LIVE, NECROTIC, FieldFrame, RunOutputs, cells_table
from ..records.observables import compute_observables
from .diffusion import Boundary, ftcs_stable_substeps, ftcs_step, implicit_step
from .mechanics import Domain, pair_forces, pairs_within
from .phenotype import behaviour_supported, build_rulesets, signal_supported

UPDATE_ORDERS = ("random", "id", "reverse", "synchronous")
SUPPORTED_EVENTS = ("set_substrate_boundary", "set_substrate", "add_cells")


def refusals(model: IRModel, knobs: Knobs) -> list[str]:
    """Reasons the reference engine cannot run this model with these knobs; empty when it can."""
    out: list[str] = []
    if model.space.dimensions != 2:
        out.append("the reference engine is 2D only")
    if model.intracellular:
        out.append("the reference engine does not run intracellular models")
    if model.native_extensions:
        out.append("native extensions (engine-specific code) cannot run in the reference engine")
    for ct in model.cell_types:
        if ct.cycle.model != "live":
            out.append(f"cell type {ct.name!r}: the reference engine runs the live cycle model only")
        if ct.interactions.active:
            out.append(
                f"cell type {ct.name!r}: attack, damage, phagocytosis and fusion are not in the reference engine "
                "(planned for milestone 3)"
            )
        if any(r > 0 for r in ct.transformations.values()):
            out.append(f"cell type {ct.name!r}: transformations are not in the reference engine")
    for i, r in enumerate(model.rules):
        if not signal_supported(r.signal, model):
            out.append(f"rules[{i}]: the reference engine has no signal {r.signal!r}")
        if not behaviour_supported(r.behaviour, model):
            out.append(f"rules[{i}]: the reference engine has no behaviour {r.behaviour!r}")
    for i, ev in enumerate(model.events):
        if ev.action not in SUPPORTED_EVENTS:
            out.append(f"events[{i}]: the reference engine does not support {ev.action!r}")
    if knobs.update_order is not None and knobs.update_order not in UPDATE_ORDERS:
        out.append(f"update_order {knobs.update_order!r} is not one of {', '.join(UPDATE_ORDERS)}")
    periodic = model.space.boundary == "periodic"
    if (knobs.grid_offset != (0.0, 0.0) or knobs.grid_rotation % 360) and not periodic:
        out.append("the grid knobs need a periodic domain (space.boundary: periodic)")
    b = model.space.bounds
    v = model.space.voxel
    square = math.isclose(b.x_max - b.x_min, b.y_max - b.y_min) and math.isclose(v.dx, v.dy)
    if knobs.grid_rotation % 180 and not square:
        out.append("grid_rotation needs a square domain with square voxels")
    explicit = any(p.kind in ("positions", "positions_csv") for p in model.initial_conditions.placements)
    if knobs.pop_scale != 1.0 and explicit:
        out.append("pop_scale cannot scale explicit initial positions")
    return out


@dataclass
class _Types:
    names: list[str]
    radius: np.ndarray
    volume: np.ndarray
    rel_adh: np.ndarray
    affinity: np.ndarray
    chemo_sub: np.ndarray
    chemo_dir: np.ndarray
    apo_duration: np.ndarray
    nec_duration: np.ndarray


def _types(model: IRModel) -> _Types:
    names = [c.name for c in model.cell_types]
    subs = [s.name for s in model.substrates]
    n = len(names)
    aff = np.ones((n, n))
    chemo_sub = np.full(n, -1)
    chemo_dir = np.zeros(n)
    for i, ct in enumerate(model.cell_types):
        for other, a in ct.mechanics.adhesion_affinities.items():
            aff[i, names.index(other)] = a
        if ct.motility.chemotaxis is not None:
            chemo_sub[i] = subs.index(ct.motility.chemotaxis.substrate)
            chemo_dir[i] = ct.motility.chemotaxis.direction
    cts = model.cell_types
    return _Types(
        names,
        np.array([c.radius for c in cts]),
        np.array([c.volume for c in cts]),
        np.array([c.mechanics.max_adhesion_distance for c in cts]),
        aff,
        chemo_sub,
        chemo_dir,
        np.array([c.death.apoptosis_duration for c in cts]),
        np.array([c.death.necrosis_duration for c in cts]),
    )


@dataclass
class _Cells:
    pos: np.ndarray
    type: np.ndarray
    id: np.ndarray
    state: np.ndarray
    death_time: np.ndarray
    v_prev: np.ndarray
    motility: np.ndarray
    beh: dict[str, np.ndarray] = field(default_factory=dict)

    @property
    def n(self) -> int:
        return len(self.type)

    def take(self, idx: np.ndarray) -> _Cells:
        return _Cells(
            self.pos[idx], self.type[idx], self.id[idx], self.state[idx], self.death_time[idx], self.v_prev[idx],
            self.motility[idx], {k: v[idx] for k, v in self.beh.items()},
        )  # fmt: skip

    def extend(self, other: _Cells) -> _Cells:
        return _Cells(
            np.concatenate([self.pos, other.pos]),
            np.concatenate([self.type, other.type]),
            np.concatenate([self.id, other.id]),
            np.concatenate([self.state, other.state]),
            np.concatenate([self.death_time, other.death_time]),
            np.concatenate([self.v_prev, other.v_prev]),
            np.concatenate([self.motility, other.motility]),
            {k: np.concatenate([v, other.beh[k]]) for k, v in self.beh.items()},
        )


class Simulation:
    """One run of an ABM-IR model. Use :func:`abmfact.reference.simulate`."""

    def __init__(self, model: IRModel, seed: int, knobs: Knobs, solver: str = "implicit"):
        reasons = refusals(model, knobs)
        if reasons:
            raise ValueError("; ".join(reasons))
        if solver not in ("implicit", "explicit"):
            raise ValueError("solver must be 'implicit' or 'explicit'")
        self.model = model
        self.knobs = knobs
        self.solver = solver
        self.warnings: list[str] = []
        exp = model.experiment
        ds = knobs.dt_scale
        self.dt_d, self.dt_m, self.dt_p = exp.dt_diffusion * ds, exp.dt_mechanics * ds, exp.dt_phenotype * ds
        self.tick = min(self.dt_d, self.dt_m, self.dt_p)
        self.k_d, self.k_m, self.k_p = (self._ratio(x, n) for x, n in ((self.dt_d, "dt_diffusion"),
                                        (self.dt_m, "dt_mechanics"), (self.dt_p, "dt_phenotype")))  # fmt: skip
        self.k_s = self._ratio(exp.save_interval, "save_interval")
        self.n_ticks = round(exp.max_time / self.tick)

        b = model.space.bounds
        centre = np.array([(b.x_min + b.x_max) / 2, (b.y_min + b.y_max) / 2])
        half = np.array([(b.x_max - b.x_min) / 2, (b.y_max - b.y_min) / 2]) * math.sqrt(knobs.pop_scale)
        self.geo_scale = math.sqrt(knobs.pop_scale)
        self.centre = centre
        self.domain = Domain(centre - half, centre + half, model.space.boundary == "periodic")
        size = self.domain.size
        self.nx = max(1, round(size[0] / (model.space.voxel.dx * knobs.dx_scale)))
        self.ny = max(1, round(size[1] / (model.space.voxel.dy * knobs.dx_scale)))
        self.dx, self.dy = size[0] / self.nx, size[1] / self.ny
        self.voxel_volume = self.dx * self.dy * model.space.voxel.dz

        if knobs.rng_streams == "independent":
            ss = np.random.SeedSequence([int(seed) & 0xFFFF_FFFF, RNG_SALT])
        else:
            ss = np.random.SeedSequence(int(seed) & 0xFFFF_FFFF)
        self.rng_init, self.rng_order, self.rng_cycle, self.rng_death, self.rng_motility, self.rng_division, \
            self.rng_events = (np.random.default_rng(s) for s in ss.spawn(7))  # fmt: skip

        self.types = _types(model)
        self.rulesets = build_rulesets(model)
        self.subs = [s.name for s in model.substrates]
        self.fields = [np.full((self.ny, self.nx), s.initial_value, dtype=float) for s in model.substrates]
        self.boundaries = [self._boundary(i) for i in range(len(self.subs))]
        self._base_boundaries = list(self.boundaries)
        self.shift = (0, 0)
        self.rot = (knobs.grid_rotation // 90) % 4
        if self.domain.periodic:
            fx, fy = knobs.grid_offset
            self.shift = (round(fx * self.nx), round(fy * self.ny))
        self.time = 0.0
        self.next_id = 0
        self.cells = self._initial_cells()
        self.pressure = np.zeros(self.cells.n)
        self.contacts = np.zeros((0, 2), dtype=np.int64)
        self.max_step_ratio = 0.0
        self.event_state: dict[int, str] = {}

    # -- set-up ------------------------------------------------------------------------------------------

    def _ratio(self, step: float, name: str) -> int:
        k = max(1, round(step / self.tick))
        if not math.isclose(k * self.tick, step, rel_tol=1e-6):
            self.warnings.append(f"{name} is not a whole multiple of the smallest step; rounded to {k * self.tick:g}")
        return k

    def _boundary(self, i: int) -> Boundary:
        if self.domain.periodic:
            return Boundary(periodic=True)
        bc = self.model.substrates[i].boundary
        return Boundary(
            x_min=bc.value_at("x_min"),
            x_max=bc.value_at("x_max"),
            y_min=bc.value_at("y_min"),
            y_max=bc.value_at("y_max"),
        )

    def _to_internal(self, pos: np.ndarray) -> np.ndarray:
        """World frame to simulation frame: rotate about the centre, then shift by whole voxels."""
        p = pos - self.centre
        for _ in range(self.rot):
            p = np.stack([-p[:, 1], p[:, 0]], axis=1)
        p = p + self.centre + np.array([self.shift[0] * self.dx, self.shift[1] * self.dy])
        return self.domain.wrap(p)

    def _to_world(self, pos: np.ndarray) -> np.ndarray:
        p = pos - np.array([self.shift[0] * self.dx, self.shift[1] * self.dy]) - self.centre
        for _ in range(self.rot):
            p = np.stack([p[:, 1], -p[:, 0]], axis=1)
        return self.domain.wrap(p + self.centre)

    def _field_to_world(self, f: np.ndarray) -> np.ndarray:
        f = np.roll(f, (-self.shift[1], -self.shift[0]), axis=(0, 1))
        return np.rot90(f, -self.rot) if self.rot else f

    def _placement_positions(self, p, rng: np.random.Generator) -> tuple[np.ndarray, list[str]]:
        g = self.geo_scale
        centre = self.centre + (np.asarray(p.centre, dtype=float) - self.centre) * g
        if p.kind == "positions":
            pts = np.asarray(p.positions, dtype=float)
            return pts, [p.cell_type] * len(pts)
        if p.kind == "positions_csv":
            return self._read_positions(p.path)
        if p.kind == "disc":
            area = math.pi * (p.radius * g) ** 2
        elif p.kind == "rectangle":
            area = p.size[0] * p.size[1] * g * g
        else:
            area = float(np.prod(self.domain.size))
        n = round(p.count * self.knobs.pop_scale) if p.count is not None else round(p.density * area)
        if p.kind == "disc":
            r = p.radius * g * np.sqrt(rng.random(n))
            theta = 2 * math.pi * rng.random(n)
            pts = centre + np.stack([r * np.cos(theta), r * np.sin(theta)], axis=1)
        elif p.kind == "rectangle":
            pts = centre + (rng.random((n, 2)) - 0.5) * np.asarray(p.size) * g
        else:
            pts = self.domain.lo + rng.random((n, 2)) * self.domain.size
        return pts, [p.cell_type] * n

    def _read_positions(self, path: str) -> tuple[np.ndarray, list[str]]:
        """PhysiCell's cells.csv: ``x,y,z,type`` with or without a header; type is a name or an index."""
        rows = list(csv.reader(self.model.resolve(path).read_text(encoding="utf-8").splitlines()))
        rows = [r for r in rows if r and not r[0].startswith("//")]
        if rows and not _is_number(rows[0][0]):
            rows = rows[1:]
        pts = np.array([[float(r[0]), float(r[1])] for r in rows]).reshape(-1, 2)
        names = [self.types.names[int(r[3])] if _is_number(r[3]) else r[3].strip() for r in rows]
        return pts, names

    def _new_cells(self, pos: np.ndarray, type_idx: np.ndarray) -> _Cells:
        n = len(type_idx)
        ids = np.arange(self.next_id, self.next_id + n)
        self.next_id += n
        cells = _Cells(
            pos, type_idx.astype(np.int64), ids, np.full(n, LIVE, dtype=np.int8), np.zeros(n), np.zeros((n, 2)),
            np.zeros((n, 2)),
        )  # fmt: skip
        cells.beh = self._base_behaviours(type_idx)
        return cells

    def _base_behaviours(self, type_idx: np.ndarray) -> dict[str, np.ndarray]:
        cts = self.model.cell_types
        beh: dict[str, np.ndarray] = {}

        def per_type(fn) -> np.ndarray:
            return np.array([fn(ct) for ct in cts], dtype=float)[type_idx] if len(cts) else np.zeros(0)

        beh["cycle entry"] = per_type(lambda c: (c.cycle.rates or [c.cycle.division_rate])[0])
        beh["apoptosis"] = per_type(lambda c: c.death.apoptosis_rate)
        beh["necrosis"] = per_type(lambda c: c.death.necrosis_rate)
        beh["migration speed"] = per_type(lambda c: c.motility.speed)
        beh["migration bias"] = per_type(lambda c: c.motility.migration_bias)
        beh["migration persistence time"] = per_type(lambda c: c.motility.persistence_time)
        beh["cell-cell adhesion"] = per_type(lambda c: c.mechanics.adhesion_strength)
        beh["cell-cell repulsion"] = per_type(lambda c: c.mechanics.repulsion_strength)
        for s in self.subs:

            def sec(c, attr, sub=s, default=0.0):
                return next((getattr(x, attr) for x in c.secretion if x.substrate == sub), default)

            beh[f"{s} secretion"] = per_type(lambda c, s=s: sec(c, "secretion_rate", s))
            beh[f"{s} secretion target"] = per_type(lambda c, s=s: sec(c, "target", s, 1.0))
            beh[f"{s} uptake"] = per_type(lambda c, s=s: sec(c, "uptake_rate", s))
            beh[f"{s} export"] = per_type(lambda c, s=s: sec(c, "net_export_rate", s))
        return beh

    def _initial_cells(self) -> _Cells:
        all_pos, all_types = [np.zeros((0, 2))], []
        for p in self.model.initial_conditions.placements:
            pts, names = self._placement_positions(p, self.rng_init)
            all_pos.append(pts)
            all_types += names
        pos = np.concatenate(all_pos)
        type_idx = np.array([self.types.names.index(t) for t in all_types], dtype=np.int64)
        if self.knobs.agent_order == "shuffled":
            perm = np.random.default_rng(self.knobs.agent_perm_seed or 0).permutation(len(type_idx))
            pos, type_idx = pos[perm], type_idx[perm]
        return self._new_cells(self._to_internal(pos), type_idx)

    # -- helpers -----------------------------------------------------------------------------------------

    def _order(self, n: int) -> np.ndarray:
        mode = self.knobs.update_order or "random"
        if mode in ("random", "synchronous"):
            return self.rng_order.permutation(n)
        idx = np.argsort(self.cells.id, kind="stable")
        return idx if mode == "id" else idx[::-1]

    def _draw(self, rng: np.random.Generator, order: np.ndarray) -> np.ndarray:
        """One uniform number per cell; the j-th draw goes to the j-th cell in activation order."""
        u = np.empty(len(order))
        u[order] = rng.random(len(order))
        return u

    def _voxel(self, pos: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        ix = np.clip(((pos[:, 0] - self.domain.lo[0]) / self.dx).astype(np.int64), 0, self.nx - 1)
        iy = np.clip(((pos[:, 1] - self.domain.lo[1]) / self.dy).astype(np.int64), 0, self.ny - 1)
        return ix, iy

    def _gradient(self, k: int) -> tuple[np.ndarray, np.ndarray]:
        f = self.fields[k]
        if self.domain.periodic:
            gx = (np.roll(f, -1, axis=1) - np.roll(f, 1, axis=1)) / (2 * self.dx)
            gy = (np.roll(f, -1, axis=0) - np.roll(f, 1, axis=0)) / (2 * self.dy)
            return gx, gy
        gy, gx = np.gradient(f, self.dy, self.dx) if min(f.shape) > 1 else (np.zeros_like(f), np.zeros_like(f))
        return gx, gy

    def _signals(self, names: set[str]) -> dict[str, np.ndarray]:
        c = self.cells
        dead = c.state != LIVE
        ix, iy = self._voxel(c.pos)
        out: dict[str, np.ndarray] = {}
        for name in names:
            if name in self.subs:
                out[name] = self.fields[self.subs.index(name)][iy, ix]
            elif name == "pressure":
                out[name] = self.pressure
            elif name == "volume":
                out[name] = self.types.volume[c.type]
            elif name in ("time", "current time", "global time"):
                out[name] = np.full(c.n, self.time)
            elif name in ("dead", "is dead"):
                out[name] = dead.astype(float)
            elif name in ("apoptotic", "is_apoptotic"):
                out[name] = (c.state == APOPTOTIC).astype(float)
            elif name in ("necrotic", "is_necrotic"):
                out[name] = (c.state == NECROTIC).astype(float)
            elif name.startswith("contact with "):
                out[name] = self._contacts(name[len("contact with ") :])
            else:
                sub = (
                    name.removesuffix(" gradient").removeprefix("grad(").removesuffix(")").removeprefix("gradient of ")
                )
                gx, gy = self._gradient(self.subs.index(sub))
                out[name] = np.hypot(gx[iy, ix], gy[iy, ix])
        return out

    def _contacts(self, what: str) -> np.ndarray:
        c = self.cells
        n = c.n
        if len(self.contacts) == 0:
            return np.zeros(n)
        i, j = self.contacts[:, 0], self.contacts[:, 1]
        if what in ("live cell", "live cells"):
            ok = c.state == LIVE
        elif what in ("dead cell", "dead cells"):
            ok = c.state != LIVE
        else:
            ok = c.type == self.types.names.index(what)
        return (np.bincount(i, weights=ok[j], minlength=n) + np.bincount(j, weights=ok[i], minlength=n)).astype(float)

    # -- processes ---------------------------------------------------------------------------------------

    def events(self) -> None:
        for k, ev in enumerate(self.model.events):
            started = self.event_state.get(k)
            if started is None and self.time >= ev.time - 1e-9:
                self.event_state[k] = "on"
                self._apply_event(ev, on=True)
            elif started == "on" and ev.until is not None and self.time >= ev.until - 1e-9:
                self.event_state[k] = "off"
                self._apply_event(ev, on=False)

    def _apply_event(self, ev, on: bool) -> None:
        if ev.action == "set_substrate":
            if on:
                self.fields[self.subs.index(ev.target)][:] = float(ev.value or 0.0)
        elif ev.action == "set_substrate_boundary":
            k = self.subs.index(ev.target)
            if self.domain.periodic:
                self.warnings.append("set_substrate_boundary has no effect on a periodic domain")
                return
            v = float(ev.value or 0.0)
            self.boundaries[k] = Boundary(v, v, v, v) if on else self._base_boundaries[k]
        elif ev.action == "add_cells" and on:
            n = int(ev.value or 0)
            pos = self.domain.lo + self.rng_events.random((n, 2)) * self.domain.size
            new = self._new_cells(pos, np.full(n, self.types.names.index(ev.target), dtype=np.int64))
            self.cells = self.cells.extend(new)
            self.pressure = np.concatenate([self.pressure, np.zeros(n)])

    def diffuse(self) -> None:
        if not self.subs:
            return
        c = self.cells
        live = c.state == LIVE
        ix, iy = self._voxel(c.pos)
        flat = iy * self.nx + ix
        frac = self.types.volume[c.type] / self.voxel_volume
        size = self.nx * self.ny
        for k, (sub, spec) in enumerate(zip(self.subs, self.model.substrates, strict=True)):
            sec = c.beh[f"{sub} secretion"] * live
            gain_c = frac * sec * c.beh[f"{sub} secretion target"] + c.beh[f"{sub} export"] * live / self.voxel_volume
            loss_c = frac * (sec + c.beh[f"{sub} uptake"] * live)
            gain = np.bincount(flat, weights=gain_c, minlength=size).reshape(self.ny, self.nx)
            loss = np.bincount(flat, weights=loss_c, minlength=size).reshape(self.ny, self.nx)
            D, decay, bc = spec.diffusion_coefficient, spec.decay_rate, self.boundaries[k]
            if self.solver == "implicit":
                self.fields[k] = implicit_step(self.fields[k], D, decay, self.dt_d, self.dx, self.dy, bc, gain, loss)
            else:
                n_sub = ftcs_stable_substeps(D, self.dt_d, self.dx, self.dy)
                h = self.dt_d / n_sub
                f = (self.fields[k] + self.dt_d * gain) / (1.0 + self.dt_d * loss)
                for _ in range(n_sub):
                    f = ftcs_step(f, D, decay, h, self.dx, self.dy, bc)
                self.fields[k] = f

    def phenotype(self) -> None:
        c = self.cells
        if c.n == 0:
            return
        needed = {s for rs in self.rulesets.values() for s in rs.signals}
        signals = self._signals(needed)
        dead = c.state != LIVE
        for (tname, behaviour), rs in self.rulesets.items():
            sel = c.type == self.types.names.index(tname)
            if not sel.any():
                continue
            target = "cycle entry" if behaviour == "exit from cycle phase 0" else behaviour
            current = c.beh[target][sel]
            c.beh[target][sel] = rs.evaluate({k: v[sel] for k, v in signals.items()}, dead[sel], current)
        dt = self.dt_p
        order = self._order(c.n)
        u_apo = self._draw(self.rng_death, order)
        u_nec = self._draw(self.rng_death, order)
        u_div = self._draw(self.rng_cycle, order)
        angle = 2 * math.pi * self._draw(self.rng_division, order)
        live = c.state == LIVE
        apo = live & (u_apo < -np.expm1(-np.maximum(c.beh["apoptosis"], 0) * dt))
        nec = live & ~apo & (u_nec < -np.expm1(-np.maximum(c.beh["necrosis"], 0) * dt))
        div = live & ~apo & ~nec & (u_div < -np.expm1(-np.maximum(c.beh["cycle entry"], 0) * dt))
        c.state[apo] = APOPTOTIC
        c.state[nec] = NECROTIC
        c.death_time[apo | nec] = self.time
        c.motility[apo | nec] = 0.0
        # Removal of dead cells whose clearance time has passed.
        duration = np.where(c.state == APOPTOTIC, self.types.apo_duration[c.type], self.types.nec_duration[c.type])
        keep = (c.state == LIVE) | (self.time - c.death_time < duration)
        # Division: daughters in activation order, each half a radius either side of the mother.
        parents = order[div[order]]
        if parents.size:
            r = self.types.radius[c.type[parents]]
            u = np.stack([np.cos(angle[parents]), np.sin(angle[parents])], axis=1) * (0.5 * r)[:, None]
            daughters = c.take(parents)
            daughters.pos = self.domain.wrap(daughters.pos + u)
            daughters.id = np.arange(self.next_id, self.next_id + parents.size)
            self.next_id += parents.size
            daughters.v_prev[:] = 0.0
            daughters.motility[:] = 0.0
            c.pos[parents] = self.domain.wrap(c.pos[parents] - u)
            keep = np.concatenate([keep, np.ones(parents.size, dtype=bool)])
            c = c.extend(daughters)
            self.pressure = np.concatenate([self.pressure, self.pressure[parents]])
        if not keep.all():
            c = c.take(np.flatnonzero(keep))
            self.pressure = self.pressure[keep]
        self.cells = c
        self.contacts = np.zeros((0, 2), dtype=np.int64)

    def mechanics(self) -> None:
        c = self.cells
        n = c.n
        if n == 0:
            return
        t = self.types
        radius = t.radius[c.type]
        cutoff = 2.0 * float((t.rel_adh * t.radius).max())
        pairs = pairs_within(c.pos, cutoff, self.domain)
        pf = pair_forces(
            c.pos, radius, c.beh["cell-cell repulsion"], c.beh["cell-cell adhesion"], t.rel_adh[c.type], t.affinity,
            c.type, pairs, self.domain,
        )  # fmt: skip
        self.pressure = pf.pressure
        self.contacts = pf.neighbours
        live = c.state == LIVE
        order = self._order(n)
        u_turn = self._draw(self.rng_motility, order)
        theta = 2 * math.pi * self._draw(self.rng_motility, order)
        speed = c.beh["migration speed"] * live
        persistence = np.maximum(c.beh["migration persistence time"], 1e-12)
        turn = (speed > 0) & ((u_turn < self.dt_m / persistence) | ~np.any(c.motility != 0, axis=1))
        if turn.any():
            rand = np.stack([np.cos(theta), np.sin(theta)], axis=1)
            bias_dir = np.zeros((n, 2))
            sub_idx = t.chemo_sub[c.type]
            for k in np.unique(sub_idx[turn]):
                if k < 0:
                    continue
                sel = turn & (sub_idx == k)
                gx, gy = self._gradient(int(k))
                ix, iy = self._voxel(c.pos[sel])
                g = np.stack([gx[iy, ix], gy[iy, ix]], axis=1) * t.chemo_dir[c.type[sel]][:, None]
                norm = np.linalg.norm(g, axis=1, keepdims=True)
                bias_dir[sel] = np.divide(g, norm, out=np.zeros_like(g), where=norm > 0)
            b = c.beh["migration bias"][:, None]
            d = (1.0 - b) * rand + b * bias_dir
            norm = np.linalg.norm(d, axis=1, keepdims=True)
            d = np.divide(d, norm, out=rand.copy(), where=norm > 0)
            c.motility[turn] = (speed[:, None] * d)[turn]
        c.motility[~live] = 0.0
        v = pf.velocity + c.motility
        step = self.dt_m * (1.5 * v - 0.5 * c.v_prev)
        moved = np.linalg.norm(step, axis=1) / radius
        self.max_step_ratio = max(self.max_step_ratio, float(moved.max(initial=0.0)))
        c.pos = self.domain.wrap(c.pos + step)
        c.v_prev = v

    # -- outputs -----------------------------------------------------------------------------------------

    def _save(self, rows: dict[str, list[np.ndarray]], frames: list[FieldFrame], times: list[float]) -> None:
        c = self.cells
        world = self._to_world(c.pos)
        rows["time"].append(np.full(c.n, self.time))
        rows["id"].append(c.id.copy())
        rows["type"].append(c.type.copy())
        rows["x"].append(world[:, 0])
        rows["y"].append(world[:, 1])
        rows["volume"].append(self.types.volume[c.type])
        rows["state"].append(c.state.copy())
        origin = (float(self.domain.lo[0] + self.dx / 2), float(self.domain.lo[1] + self.dy / 2))
        for sub, f in zip(self.subs, self.fields, strict=True):
            frames.append(FieldFrame(self.time, sub, origin, (self.dx, self.dy), self._field_to_world(f).copy()))
        times.append(self.time)

    def run(self) -> RunOutputs:
        rows: dict[str, list[np.ndarray]] = {k: [] for k in ("time", "id", "type", "x", "y", "volume", "state")}
        frames: list[FieldFrame] = []
        times: list[float] = []
        self._save(rows, frames, times)
        for i in range(self.n_ticks):
            self.events()
            if i % self.k_d == 0:
                self.diffuse()
            if i % self.k_p == 0:
                self.phenotype()
            if i % self.k_m == 0:
                self.mechanics()
            self.time = (i + 1) * self.tick
            if (i + 1) % self.k_s == 0:
                self._save(rows, frames, times)
        if self.max_step_ratio > 0.5:
            self.warnings.append(
                f"a mechanics step moved a cell {self.max_step_ratio:.2f} radii; consider a smaller dt_mechanics"
            )
        cells = cells_table({k: np.concatenate(v) for k, v in rows.items()})
        out = RunOutputs(cells=cells, fields=frames, type_names=list(self.types.names), save_times=times)
        out.warnings = list(self.warnings)
        return compute_observables(self.model, out)


def _is_number(s: str) -> bool:
    try:
        float(s)
    except ValueError:
        return False
    return True


def simulate(model: IRModel, seed: int, knobs: Knobs | None = None, solver: str = "implicit") -> RunOutputs:
    """Run an ABM-IR model in the reference engine and return its outputs in the common schema."""
    return Simulation(model, seed, knobs or Knobs(), solver=solver).run()
