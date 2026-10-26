"""Translate between ABM-IR models and Morpheus models (tier T1), with a fidelity report.

Morpheus can say most of ABM-IR in its own language, so the translation is one MorpheusML file with no extra
code. Lengths are converted with ``node_length`` (micron per lattice node) and times with ``time_minutes``
(minutes per Morpheus time unit, which is also one Monte Carlo step); the fidelity report names both.

* Each cell keeps its state (live, apoptotic, necrotic), its damage and its behaviours as cell properties. A
  behaviour with rules is PhysiCell 1.14.2's Hill combination written as a Morpheus expression; the signals
  come from ``Mapper`` (the field under the cell) and ``NeighborhoodReporter`` (contacts and neighbours).
* Division, death and removal are ``CellDivision``, ``Event`` and ``CellDeath`` with the per-step probabilities
  of the ABM-IR rates. Fields are ``DiffEqn`` with secretion and uptake by the cells covering each node.
* A drug pulse is a pair of ``Event`` that set the boundary value while the event lasts.

Chemotaxis is ``DirectedMotion`` along the gradient's direction averaged over the cell, from two helper fields,
as ABM-IR's chemotaxis follows the gradient's direction only. A field whose diffusion is capped for speed has its
reactions slowed by the same factor, which keeps its steady profile.

What has no exact counterpart is marked ``approximated`` with a note: contact energies, the volume constraint,
motility and chemotaxis strengths, capped diffusion, pressure (the fraction of the cell's boundary touching other
cells), attack (damage from each touching effector at the steady share of time an effector spends attacking), and a
boundary that is no-flux in ABM-IR but holds a value while an event lasts.

:func:`import_model` reads a MorpheusML model back: the lattice, cell types, target areas, contact energies,
fields and circle or rectangle initialisers, with rates read where they are plain numbers. Everything else is
listed as dropped.
"""

from __future__ import annotations

import hashlib
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from ...digest import content_bytes
from ...ir import IRModel
from ...records import FidelityReport

J_MEDIUM = 10.0
ADHESION_REF = 0.4
VOLUME_STRENGTH = 1.0
TEMPERATURE = 10.0
MOTILITY_STRENGTH = 50.0  # as CompuCell3D's MOTILITY_LAMBDA, so the two translations push cells alike
MAX_DIFFUSION = 200.0
RATE_BEHAVIOURS = {"cycle entry", "exit from cycle phase 0", "apoptosis", "necrosis"}
MODEL_FILE = "model.xml"


def translator_fingerprint() -> str:
    """A hash of this module, so a changed translator never reuses an old export."""
    return hashlib.sha256(content_bytes(Path(__file__))).hexdigest()


def symbol(name: str) -> str:
    """A Morpheus symbol for an ABM-IR name: non-word characters become underscores."""
    s = re.sub(r"\W+", "_", name).strip("_") or "x"
    return s if not s[0].isdigit() else f"s_{s}"


def _sub(parent: ET.Element, tag: str, **attrs: Any) -> ET.Element:
    return ET.SubElement(parent, tag, {k.replace("_", "-"): str(v) for k, v in attrs.items()})


def _num(x: float) -> str:
    return repr(float(x))


def _hill_expr(base: float, rules: list[dict[str, Any]], sig: dict[str, str]) -> str:
    """PhysiCell 1.14.2's combination of Hill rules for one behaviour, as a Morpheus expression."""
    up = [r for r in rules if r["direction"] == "increases"]
    down = [r for r in rules if r["direction"] == "decreases"]
    hi = max([base] + [r["saturation"] for r in up])
    lo = min([base] + [r["saturation"] for r in down])

    def term(r: dict[str, Any]) -> str:
        s = sig[r["signal"]]
        if not r["apply_to_dead"]:
            s = f"if(state == 0, {s}, 0)"
        return f"(max({s}, 0) / {_num(r['half_max'])})^{_num(r['hill'])}"

    su = " + ".join(term(r) for r in up) or "0"
    sd = " + ".join(term(r) for r in down) or "0"
    b_up = f"({_num(base)} + {_num(hi - base)} * (({su}) / (1 + {su})))"
    return f"{b_up} + ({_num(lo)} - {b_up}) * (({sd}) / (1 + {sd}))"


def export_model(
    model: IRModel, out: str | Path, node_length: float = 2.0, time_minutes: float = 1.0
) -> FidelityReport:
    """Write ``model`` as a Morpheus model in the folder ``out`` and return what was kept or changed."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    s, m = float(node_length), float(time_minutes)
    rep = FidelityReport("abm-ir", f"morpheus:{out.name}", tier="T1")
    rep.add("units", "approximated", f"one lattice node is {s:g} micron and one time unit (one MCS) {m:g} min")
    if model.space.dimensions != 2:
        raise ValueError("ABM-IR models export to 2D Morpheus models only, for now")
    b = model.space.bounds
    nx, ny = max(1, round((b.x_max - b.x_min) / s)), max(1, round((b.y_max - b.y_min) / s))
    names = [symbol(c.name) for c in model.cell_types]
    if names != [c.name for c in model.cell_types]:
        rep.add("cell_types[*].name", "approximated", "renamed to Morpheus symbols")
    fields = [symbol(x.name) for x in model.substrates]

    root = ET.Element("MorpheusModel", version="5.0")
    desc = _sub(root, "Description")
    _sub(desc, "Title").text = model.name
    _sub(desc, "Details").text = f"Exported from ABM-IR by ABMfact. {model.description}".strip()

    glob = _sub(root, "Global")
    periodic = model.space.boundary == "periodic"
    pulses: dict[str, list[dict[str, Any]]] = {}
    for k, ev in enumerate(model.events):
        if ev.action == "set_substrate_boundary":
            pulses.setdefault(symbol(ev.target), []).append({"time": ev.time / m, "until": ev.until, "value": ev.value})
        else:
            rep.add(f"events[{k}]", "dropped", f"{ev.action} is not exported to Morpheus")
    for sub, name in zip(model.substrates, fields, strict=True):
        f = _sub(glob, "Field", symbol=name, value=_num(sub.initial_value), name=sub.name)
        sides = {"x": "x_max", "-x": "x_min", "y": "y_max", "-y": "y_min"}
        if not periodic:
            if name in pulses:
                bc = f"bc_{name}"
                _sub(glob, "Variable", symbol=bc, value=_num(0.0))
                for ref in sides:
                    _sub(f, "BoundaryValue", boundary=ref, value=bc, type="value")
                rep.add(f"substrates[{sub.name}].boundary", "approximated",
                        "held at the event value while it lasts and at 0 otherwise, so a no-flux edge also drains "
                        "the field between events")  # fmt: skip
            else:
                for ref, side in sides.items():
                    v = sub.boundary.value_at(side)
                    if v is not None:
                        _sub(f, "BoundaryValue", boundary=ref, value=_num(v), type="value")
        # Explicit Euler steps the reactions, so the step stays below one over the fastest rate (per step).
        k_max = sub.decay_rate
        for ct in model.cell_types:
            sec = next((x for x in ct.secretion if x.substrate == sub.name), None)
            sats = [r.saturation_value for r in model.rules if r.cell_type == ct.name
                    and r.behaviour in (f"{sub.name} secretion", f"{sub.name} uptake")]  # fmt: skip
            own = (sec.secretion_rate + sec.uptake_rate) if sec else 0.0
            k_max = max(k_max, sub.decay_rate + own + sum(sats))
        # Morpheus substeps diffusion to stay stable, so fast diffusion is capped. The field's reactions are slowed
        # by the same factor, which keeps the steady profile (its decay length sqrt(D/k)) and slows only settling.
        d_nodes = sub.diffusion_coefficient * m / (s * s)
        slow = max(1.0, d_nodes / MAX_DIFFUSION)
        if slow > 1:
            note = (f"{d_nodes:.4g} nodes^2/step capped at {MAX_DIFFUSION:g} ({slow:.3g} times slower), and the "
                    "field's secretion, uptake and decay slowed by the same factor, so its steady profile is kept and "
                    "only its settling is slower; a larger node_length keeps more of the speed")  # fmt: skip
            rep.add(f"substrates[{sub.name}].diffusion_coefficient", "approximated", note)
            d_nodes = MAX_DIFFUSION
        k_max /= slow
        dt = (
            min(model.experiment.dt_diffusion / m, 1.0 / (k_max * m))
            if k_max > 0
            else model.experiment.dt_diffusion / m
        )
        system = _sub(glob, "System", solver="Euler [fixed, O(1)]", time_step=_num(dt))
        eqn = _sub(system, "DiffEqn", symbol_ref=name)
        reaction = (f"sec_{name} * (tgt_{name} - {name}) - up_{name} * {name} + exp_{name} "
                    f"- {_num(sub.decay_rate * m)} * {name}")  # fmt: skip
        _sub(eqn, "Reaction").text = reaction if slow == 1 else f"({reaction}) / {_num(slow)}"
        _sub(eqn, "Diffusion").text = _num(d_nodes)
        rep.add(f"substrates[{sub.name}]", "exact", "diffusion, decay, initial and boundary values")
    for name, events in pulses.items():
        for pulse in events:
            start = _sub(glob, "Event", trigger="on-change", time_step="1")
            _sub(start, "Condition").text = f"time >= {_num(pulse['time'])}"
            _sub(_sub(start, "Rule", symbol_ref=f"bc_{name}"), "Expression").text = _num(pulse["value"] or 0.0)
            if pulse["until"] is not None:
                stop = _sub(glob, "Event", trigger="on-change", time_step="1")
                _sub(stop, "Condition").text = f"time >= {_num(pulse['until'] / m)}"
                _sub(_sub(stop, "Rule", symbol_ref=f"bc_{name}"), "Expression").text = _num(0.0)
        rep.add(f"events[{name}]", "exact", "the boundary value is set and reset by two Morpheus events")

    space = _sub(root, "Space")
    lattice = _sub(space, "Lattice", **{"class": "square"})
    _sub(lattice, "Size", symbol="size", value=f"{nx}, {ny}, 0")
    bcs = _sub(lattice, "BoundaryConditions")
    for axis in ("x", "y"):
        if periodic:
            _sub(bcs, "Condition", type="periodic", boundary=axis)
        else:
            # Closed edges are no-flux; a field's BoundaryValue holds its Dirichlet sides at their value.
            _sub(bcs, "Condition", type="noflux", boundary=axis)
            _sub(bcs, "Condition", type="noflux", boundary=f"-{axis}")
    _sub(_sub(lattice, "Neighborhood"), "Order").text = "2"
    _sub(space, "SpaceSymbol", symbol="space")
    rep.add("space", "exact", f"{nx} x {ny} nodes, {model.space.boundary} edges")

    time = _sub(root, "Time")
    _sub(time, "StartTime", value="0")
    _sub(time, "StopTime", value=_num(model.experiment.max_time / m))
    _sub(time, "RandomSeed", value="0")
    _sub(time, "TimeSymbol", symbol="time")
    rep.add("experiment", "exact", f"{model.experiment.max_time / m:g} time units of {m:g} minutes")

    types_el = _sub(root, "CellTypes")
    attackers = {c.name: c for c in model.cell_types if any(r > 0 for r in c.interactions.attack_rates.values())}
    pressure_used = any(r.signal == "pressure" for r in model.rules)
    gradients: set[str] = set()
    for ct, name in zip(model.cell_types, names, strict=True):
        el = _sub(types_el, "CellType", name=name, **{"class": "biological"})
        base_path = f"cell_types[{ct.name}]"
        for prop, value in (("state", 0.0), ("death_time", 0.0), ("damage", 0.0)):
            _sub(el, "Property", symbol=prop, value=_num(value))
        r = (3 * ct.volume / (4 * math.pi)) ** (1 / 3)
        target = max(1.0, round(math.pi * r * r / (s * s)))
        _sub(el, "VolumeConstraint", strength=_num(VOLUME_STRENGTH), target=_num(target))
        rep.add(
            f"{base_path}.volume", "approximated", f"a target area of {target:g} nodes, strength {VOLUME_STRENGTH:g}"
        )
        sig: dict[str, str] = {"pressure": "contact / max(perimeter, 1)", "damage": "damage", "dead": "(state > 0)",
                               "is dead": "(state > 0)", "time": f"time * {_num(m)}"}  # fmt: skip
        for sub, fname in zip(model.substrates, fields, strict=True):
            prop = f"at_{fname}"
            _sub(el, "Property", symbol=prop, value="0")
            mapper = _sub(el, "Mapper")
            _sub(mapper, "Input", value=fname)
            _sub(mapper, "Output", symbol_ref=prop, mapping="average")
            sig[sub.name] = prop
        rules_here = [r for r in model.rules if r.cell_type == ct.name]
        for rr in rules_here:
            if rr.signal.startswith("contact with ") and rr.signal not in sig:
                other = rr.signal[len("contact with ") :]
                prop = f"n_{symbol(other)}"
                _sub(el, "Property", symbol=prop, value="0")
                rep_el = _sub(el, "NeighborhoodReporter")
                cond = ("cell.type != celltype.medium.id" if other in ("live cell", "live cells", "cell", "cells")
                        else f"cell.type == celltype.{symbol(other)}.id")  # fmt: skip
                _sub(rep_el, "Input", value=cond, scaling="cell")
                _sub(rep_el, "Output", symbol_ref=prop, mapping="sum")
                sig[rr.signal] = prop
        if pressure_used:
            for prop, reported in (("contact", "cell.type != celltype.medium.id"), ("perimeter", "1")):
                _sub(el, "Property", symbol=prop, value="0")
                rep_el = _sub(el, "NeighborhoodReporter")
                _sub(rep_el, "Input", value=reported, scaling="length")
                _sub(rep_el, "Output", symbol_ref=prop, mapping="sum")
        unknown = sorted({rr.signal for rr in rules_here if rr.signal not in sig})
        if unknown:
            rep.add(f"{base_path}.rules", "dropped", f"signals not exported to Morpheus: {', '.join(unknown)}")
            rules_here = [rr for rr in rules_here if rr.signal in sig]
        base = {
            "cycle entry": (ct.cycle.rates or [ct.cycle.division_rate])[0] * m,
            "apoptosis": ct.death.apoptosis_rate * m,
            "necrosis": ct.death.necrosis_rate * m,
            "migration speed": ct.motility.speed * m / s,
            "migration bias": ct.motility.migration_bias,
        }
        for sub, fname in zip(model.substrates, fields, strict=True):
            sec = next((x for x in ct.secretion if x.substrate == sub.name), None)
            base[f"{sub.name} secretion"] = (sec.secretion_rate if sec else 0.0) * m
            base[f"{sub.name} uptake"] = (sec.uptake_rate if sec else 0.0) * m
            _sub(el, "Constant", symbol=f"tgt_{fname}", value=_num(sec.target if sec else 1.0))
            _sub(el, "Constant", symbol=f"exp_{fname}", value=_num((sec.net_export_rate if sec else 0.0) * m))
        by_behaviour: dict[str, list[dict[str, Any]]] = {}
        for rr in rules_here:
            beh = "cycle entry" if rr.behaviour == "exit from cycle phase 0" else rr.behaviour
            sat = rr.saturation_value
            if beh in RATE_BEHAVIOURS or beh.endswith((" secretion", " uptake")):
                sat *= m
            elif beh == "migration speed":
                sat *= m / s
            entry = {"signal": rr.signal, "direction": rr.direction, "saturation": sat, "half_max": rr.half_max,
                     "hill": rr.hill_power, "apply_to_dead": rr.apply_to_dead}  # fmt: skip
            by_behaviour.setdefault(beh, []).append(entry)
        dropped_behaviours = sorted(set(by_behaviour) - set(base))
        if dropped_behaviours:
            rep.add(f"{base_path}.rules", "dropped", f"behaviours not exported: {', '.join(dropped_behaviours)}")
        live = "(state == 0)"
        beh_sym = {"cycle entry": "r_cycle", "apoptosis": "r_apoptosis", "necrosis": "r_necrosis",
                   "migration speed": "speed", "migration bias": "bias"}  # fmt: skip
        for sub, fname in zip(model.substrates, fields, strict=True):
            beh_sym[f"{sub.name} secretion"] = f"sec_{fname}"
            beh_sym[f"{sub.name} uptake"] = f"up_{fname}"
        for beh, sym in beh_sym.items():
            # The base value is kept as a constant too, so an import reads it back even when rules change it.
            _sub(el, "Constant", symbol=f"base_{sym}", value=_num(base[beh]))
            expr = _hill_expr(base[beh], by_behaviour[beh], sig) if beh in by_behaviour else f"base_{sym}"
            if sym.startswith(("sec_", "up_")):
                expr = f"{live} * ({expr})"
            _sub(_sub(el, "Function", symbol=sym), "Expression").text = expr
        if rules_here:
            rep.add(f"{base_path}.rules", "exact", "Hill-type rules combined as in PhysiCell 1.14.2")

        div = _sub(el, "CellDivision", division_plane="random")
        _sub(div, "Condition").text = f"{live} and rand_uni(0, 1) < r_cycle"
        for state, rate_sym in ((1, "r_apoptosis"), (2, "r_necrosis")):
            dies = _sub(el, "Event", trigger="when-true", time_step="1")
            _sub(dies, "Condition").text = f"{live} and rand_uni(0, 1) < {rate_sym}"
            _sub(_sub(dies, "Rule", symbol_ref="state"), "Expression").text = str(state)
            _sub(_sub(dies, "Rule", symbol_ref="death_time"), "Expression").text = "time"
        death = _sub(el, "CellDeath")
        _sub(death, "Condition").text = (
            f"(state == 1 and time - death_time >= {_num(ct.death.apoptosis_duration / m)}) or "
            f"(state == 2 and time - death_time >= {_num(ct.death.necrosis_duration / m)})"
        )
        rep.add(f"{base_path}.cycle", "exact" if ct.cycle.model == "live" else "approximated", "the live cycle")
        rep.add(f"{base_path}.death", "approximated", "dead cells keep their size until removal, then are removed")

        if ct.motility.speed > 0:
            pm = _sub(el, "PersistentMotion", decay_time=_num(ct.motility.persistence_time / m),
                      strength=f"{live} * {_num(MOTILITY_STRENGTH)} * speed * (1 - bias)")  # fmt: skip
            del pm
            rep.add(f"{base_path}.motility", "approximated",
                    f"persistent motion of strength {MOTILITY_STRENGTH:g} x speed; the speed reached depends on the "
                    "temperature and the volume constraint")  # fmt: skip
        chemo = ct.motility.chemotaxis
        if chemo is not None and ct.motility.migration_bias > 0:
            # ABM-IR chemotaxis follows the gradient's direction only, as PhysiCell does. The cell's direction is the
            # gradient averaged over its nodes, and DirectedMotion normalises it, so steep and shallow gradients
            # push alike, at the persistent motion's strength.
            fname = symbol(chemo.substrate)
            for axis in ("x", "y"):
                _sub(el, "Property", symbol=f"cell_grad_{axis}_{fname}", value="0")
                mapper = _sub(el, "Mapper")
                _sub(mapper, "Input", value=f"grad_{axis}_{fname}")
                _sub(mapper, "Output", symbol_ref=f"cell_grad_{axis}_{fname}", mapping="average")
            sign = "" if chemo.direction > 0 else "-"
            _sub(el, "DirectedMotion", direction=f"{sign}cell_grad_x_{fname}, {sign}cell_grad_y_{fname}, 0",
                 strength=f"{live} * {_num(MOTILITY_STRENGTH)} * speed * bias")  # fmt: skip
            note = (f"DirectedMotion along the gradient's direction averaged over the cell, of strength "
                    f"{MOTILITY_STRENGTH:g} x speed x bias as for persistent motion; the speed reached depends on the "
                    "temperature and the volume constraint")  # fmt: skip
            rep.add(f"{base_path}.motility.chemotaxis", "approximated", note)
            gradients.add(fname)

        incoming = [(a, a.interactions.attack_rates.get(ct.name, 0.0)) for a in attackers.values()]
        incoming = [(a, rate) for a, rate in incoming if rate > 0]
        growth, repair = ct.integrity.damage_rate * m, ct.integrity.damage_repair_rate * m
        if incoming or growth > 0 or repair > 0:
            terms = [_num(growth), f"- {_num(repair)} * damage"]
            for a, rate in incoming:
                prop = f"n_{symbol(a.name)}_attackers"
                _sub(el, "Property", symbol=prop, value="0")
                rep_el = _sub(el, "NeighborhoodReporter")
                live_effector = f"cell.type == celltype.{symbol(a.name)}.id and state == 0"
                _sub(rep_el, "Input", value=live_effector, scaling="cell")
                _sub(rep_el, "Output", symbol_ref=prop, mapping="sum")
                p = rate * ct.interactions.immunogenicities.get(a.name, 1.0) * a.interactions.attack_duration
                share = p / (1 + p)
                terms.append(f"+ {live} * {prop} * {_num(a.interactions.damage_rate * m * share)}")
            system = _sub(el, "System", solver="Euler [fixed, O(1)]", time_step="1")
            _sub(_sub(system, "DiffEqn", symbol_ref="damage"), "Reaction").text = " ".join(terms)
            if incoming:
                rep.add(f"{base_path}.damage", "approximated",
                        "each touching live effector adds damage at its damage rate times the steady share of time an "
                        "effector spends attacking, rate x duration / (1 + rate x duration)")  # fmt: skip
        it = ct.interactions
        if it.phagocytosis_rates or it.fusion_rates or it.dead_phagocytosis_rate > 0:
            rep.add(f"{base_path}.interactions", "dropped", "phagocytosis and fusion are not exported")
        if any(v > 0 for v in ct.transformations.values()):
            rep.add(f"{base_path}.transformations", "dropped", "transformations are not exported")

    for fname in sorted(gradients):
        # Central differences over the lattice neighbourhood, written to two fields each step; on a periodic
        # lattice a neighbour across the edge is one node away, not the lattice width.
        for axis, size in (("x", nx), ("y", ny)):
            _sub(glob, "Field", symbol=f"grad_{axis}_{fname}", value="0")
            d = f"(space.{axis} - local.space.{axis})"
            if periodic:
                d = f"if({d} > 1, {d} - {size}, if({d} < -1, {d} + {size}, {d}))"
            reporter = _sub(glob, "NeighborhoodReporter")
            _sub(reporter, "Input", value=f"{fname} * {d}", scaling="cell")
            _sub(reporter, "Output", symbol_ref=f"grad_{axis}_{fname}", mapping="sum")
    medium = _sub(types_el, "CellType", name="medium", **{"class": "medium"})
    for fname in fields:
        for pre in ("sec_", "up_", "exp_", "tgt_"):
            _sub(medium, "Constant", symbol=f"{pre}{fname}", value="0")
    for prop in ("state", "death_time", "damage"):
        _sub(medium, "Constant", symbol=prop, value="0")
    rep.add("cell_types[*].mechanics", "approximated",
            f"contact energies J(a,b) = 2*{J_MEDIUM:g} - {J_MEDIUM:g}*e/{ADHESION_REF:g} (at least 2), medium "
            f"{J_MEDIUM:g}")  # fmt: skip

    cpm = _sub(root, "CPM")
    inter = _sub(cpm, "Interaction", default="0.0")
    for n in names:
        _sub(inter, "Contact", type1=n, type2="medium", value=_num(J_MEDIUM))
    for i, a in enumerate(model.cell_types):
        for bt in model.cell_types[i:]:
            ma, mb = a.mechanics, bt.mechanics
            e = math.sqrt(ma.adhesion_strength * ma.adhesion_affinities.get(bt.name, 1.0)
                          * mb.adhesion_strength * mb.adhesion_affinities.get(a.name, 1.0))  # fmt: skip
            _sub(inter, "Contact", type1=symbol(a.name), type2=symbol(bt.name),
                 value=_num(round(max(2.0, 2 * J_MEDIUM - J_MEDIUM * e / ADHESION_REF), 6)))  # fmt: skip
    sampler = _sub(cpm, "MonteCarloSampler", stepper="edgelist")
    _sub(sampler, "MCSDuration", value="1")
    _sub(_sub(sampler, "Neighborhood"), "Order").text = "2"
    _sub(sampler, "MetropolisKinetics", temperature=_num(TEMPERATURE))
    shape = _sub(cpm, "ShapeSurface", scaling="norm")
    _sub(_sub(shape, "Neighborhood"), "Order").text = "2"

    pops = _sub(root, "CellPopulations")
    for pl in model.initial_conditions.placements:
        if pl.cell_type is None or pl.kind in ("positions", "positions_csv"):
            rep.add("initial_conditions.placements", "dropped", f"{pl.kind} placements are not exported to Morpheus")
            continue
        count = pl.count if pl.count is not None else 0
        if pl.count is None and pl.density is not None:
            area = (math.pi * (pl.radius or 0) ** 2 if pl.kind == "disc" else
                    (pl.size[0] * pl.size[1] if pl.size else 0) if pl.kind == "rectangle" else
                    (b.x_max - b.x_min) * (b.y_max - b.y_min))  # fmt: skip
            count = round(pl.density * area)
        pop = _sub(pops, "Population", type=symbol(pl.cell_type), size="0")
        cx, cy = (pl.centre[0] - b.x_min) / s, (pl.centre[1] - b.y_min) / s
        if pl.kind == "disc":
            init = _sub(pop, "InitCircle", number_of_cells=str(count), mode="random")
            _sub(init, "Dimensions", radius=_num((pl.radius or 0) / s), center=f"{cx:g}, {cy:g}, 0")
        else:
            w, h = pl.size if pl.kind == "rectangle" and pl.size else (b.x_max - b.x_min, b.y_max - b.y_min)
            ox, oy = (cx - w / s / 2, cy - h / s / 2) if pl.kind == "rectangle" else (0.0, 0.0)
            init = _sub(pop, "InitRectangle", number_of_cells=str(count), mode="random")
            _sub(init, "Dimensions", origin=f"{ox:g}, {oy:g}, 0", size=f"{w / s:g}, {h / s:g}, 0")
    rep.add("initial_conditions.placements", "approximated", "Morpheus seeds each cell and grows it to size")
    _sub(root, "Analysis")
    if model.intracellular:
        rep.add("intracellular", "dropped", "intracellular models are not exported to Morpheus yet")
    for k, x in enumerate(model.native_extensions):
        rep.add(f"native_extensions[{k}]", "dropped", f"{x.engine} code does not run in Morpheus")
    ET.ElementTree(root).write(out / MODEL_FILE, encoding="unicode")
    return rep


def _float(text: str | None) -> float | None:
    try:
        return float(text) if text is not None and text.strip() else None
    except ValueError:
        return None


def _numeric_function(cell_type: ET.Element, name: str) -> float | None:
    """A cell type's base value for ``name``: the ``base_<name>`` constant an export writes, or a plain number."""
    base = _float(_attr(cell_type, f"Constant[@symbol='base_{name}']"))
    if base is not None:
        return base
    fn = cell_type.find(f"Function[@symbol='{name}']/Expression")
    if fn is not None:
        return _float(fn.text)
    return _float(_attr(cell_type, f"Constant[@symbol='{name}']"))


def _arith(expr: str, names: dict[str, float]) -> float:
    """Evaluate arithmetic on numbers and ``names``, such as ``size.x/2``; anything else raises ValueError."""
    import ast
    import operator

    ops: dict[type, Any] = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                            ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg}  # fmt: skip
    expr = expr.strip()

    def walk(node: ast.AST) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
            return float(node.value)
        if isinstance(node, ast.Name) and node.id in names:
            return names[node.id]
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            dotted = f"{node.value.id}.{node.attr}"
            if dotted in names:
                return names[dotted]
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            return float(ops[type(node.op)](walk(node.left), walk(node.right)))
        if isinstance(node, ast.UnaryOp) and type(node.op) in ops:
            return float(ops[type(node.op)](walk(node.operand)))
        raise ValueError(f"not plain arithmetic: {expr!r}")

    try:
        return walk(ast.parse(expr.replace("^", "**"), mode="eval").body)
    except SyntaxError as exc:
        raise ValueError(f"not plain arithmetic: {expr!r}") from exc


def _attr(parent: ET.Element, path: str, key: str = "value") -> str | None:
    el = parent.find(path)
    return el.get(key) if el is not None else None


def import_model(
    path: str | Path, node_length: float = 1.0, time_minutes: float = 1.0
) -> tuple[IRModel, FidelityReport]:
    """Read a MorpheusML model into ABM-IR, with what was kept, approximated or dropped.

    Lattice nodes become ``node_length`` micron and Morpheus time units ``time_minutes`` minutes; with the
    defaults the model stays in its own units. Rates and speeds are read only where the model gives them as
    plain numbers, as an export from ABM-IR does; any other expression is reported as dropped.
    """
    from ...digest import content_digest
    from ...ir import model_from_dict
    from .model import MorpheusModel

    mm = MorpheusModel.read(path)
    s, m = float(node_length), float(time_minutes)
    rep = FidelityReport(f"morpheus:{mm.path.name}", "abm-ir", tier="T1")
    rep.add("units", "approximated" if (s, m) != (1.0, 1.0) else "exact",
            f"one lattice node is {s:g} micron and one time unit {m:g} min")  # fmt: skip
    root = mm.root
    nx, ny, nz = mm.lattice()
    if mm.dimensions() != 2:
        raise ValueError("only 2D Morpheus models import to ABM-IR, for now")
    conditions = [c.get("type") for c in root.iterfind("Space/Lattice/BoundaryConditions/Condition")]
    periodic = bool(conditions) and all(c == "periodic" for c in conditions)
    doc: dict[str, Any] = {
        "schema_version": "0.1",
        "name": symbol(mm.title()),
        "description": f"Imported from the Morpheus model {mm.path.name}.",
        "space": {
            "dimensions": 2,
            "bounds": {"x_min": 0, "x_max": nx * s, "y_min": 0, "y_max": ny * s, "z_min": 0, "z_max": s},
            "voxel": {"dx": s, "dy": s, "dz": s},
            "boundary": "periodic" if periodic else "closed",
        },
    }
    rep.add("space", "exact", f"{nx} x {ny} nodes, {'periodic' if periodic else 'closed'} edges")

    medium = next((ct.get("name", "") for ct in root.iterfind("CellTypes/CellType") if ct.get("class") == "medium"),
                  "medium")  # fmt: skip
    energies: dict[frozenset[str], float] = {}
    for c in root.iterfind("CPM/Interaction/Contact"):
        value = _float(c.get("value"))
        if value is not None:
            energies[frozenset((c.get("type1", ""), c.get("type2", "")))] = value
    types = mm.biological_types()
    handled = {"Property", "Constant", "VolumeConstraint", "Function", "CellDivision", "CellDeath", "Event",
               "PersistentMotion", "Chemotaxis"}  # fmt: skip
    cell_types = []
    for t in types:
        el = root.find(f"CellTypes/CellType[@name='{t}']")
        assert el is not None
        area_nodes = _float(_attr(el, "VolumeConstraint", "target"))
        if area_nodes is None:
            rep.add(f"cell_types[{t}].volume", "approximated", "the target volume is not a number; 25 nodes is used")
            area_nodes = 25.0
        area = area_nodes * s * s
        j_medium = energies.get(frozenset((medium, t)), J_MEDIUM)
        strength = {}
        for other in types:
            j = energies.get(frozenset((t, other)))
            if j is not None:
                strength[other] = max(0.0, (2.0 * j_medium - j) / j_medium * ADHESION_REF)
        own = strength.get(t, ADHESION_REF)
        rates = {k: _numeric_function(el, k) for k in ("r_cycle", "r_apoptosis", "r_necrosis", "speed", "bias")}
        if el.find("CellDivision") is not None and rates["r_cycle"] is None:
            rep.add(f"cell_types[{t}].cycle", "dropped", "the division condition is a Morpheus expression")
        if rates["r_apoptosis"] is None and rates["r_necrosis"] is None and el.find("CellDeath") is not None:
            rep.add(f"cell_types[{t}].death", "dropped", "the death condition is a Morpheus expression")
        ct: dict[str, Any] = {
            "name": t,
            "volume": 4.0 / 3.0 * math.pi * (area / math.pi) ** 1.5,
            "cycle": {"model": "live", "rates": [(rates["r_cycle"] or 0.0) / m]},
            "death": {"apoptosis_rate": (rates["r_apoptosis"] or 0.0) / m,
                      "necrosis_rate": (rates["r_necrosis"] or 0.0) / m},
            "mechanics": {"adhesion_strength": own,
                          "adhesion_affinities": {o: (v / own if own > 0 else 0.0) for o, v in strength.items()}},
        }  # fmt: skip
        if rates["speed"]:
            pt = _float(_attr(el, "PersistentMotion", "decay-time"))
            ct["motility"] = {"speed": rates["speed"] * s / m, "persistence_time": (pt or 1.0) * m,
                              "migration_bias": rates["bias"] or 0.0}  # fmt: skip
            rep.add(f"cell_types[{t}].motility", "approximated", "speed and persistence from persistent motion")
        cell_types.append(ct)
        for tag in dict.fromkeys(c.tag for c in el if isinstance(c.tag, str) and c.tag not in handled):
            rep.add(f"cell_types[{t}].{tag}", "dropped", "no ABM-IR reading of this Morpheus element yet")
        for fn in el.iterfind("Function"):
            if fn.get("symbol") in rates and rates[fn.get("symbol", "")] is None:
                rep.add(f"cell_types[{t}].{fn.get('symbol')}", "dropped", "a Morpheus expression, such as a rule")
    doc["cell_types"] = cell_types
    rep.add("cell_types[*].volume", "approximated", "target areas in nodes, read as the volume of a sphere")
    if energies:
        rep.add("cell_types[*].mechanics", "approximated",
                "adhesion strengths and affinities from contact energies, inverting the export mapping")  # fmt: skip

    substrates = []
    for f in root.iterfind("Global/Field"):
        name = f.get("symbol", "")
        ir_name = f.get("name") or name
        initial = _float(f.get("value"))
        if initial is None:
            rep.add(
                f"substrates[{ir_name}].initial_value",
                "approximated",
                f"the expression {f.get('value')!r} is read as 0",
            )
        eqn = root.find(f"Global/System/DiffEqn[@symbol-ref='{name}']")
        d = _float(_attr(f, "Diffusion", "rate"))
        if d is None and eqn is not None:
            d = _float(eqn.findtext("Diffusion"))
        decay = 0.0
        reaction = (eqn.findtext("Reaction") or "").strip() if eqn is not None else ""
        found = re.search(rf"- ([0-9.eE+-]+) \* {re.escape(name)}$", reaction)
        if found:
            decay = float(found.group(1))
        elif reaction:
            rep.add(f"substrates[{ir_name}].reactions", "dropped", f"the reaction {reaction!r} is not translated")
        sides: dict[str, float | None] = {}
        names = {"x": "x_max", "-x": "x_min", "y": "y_max", "-y": "y_min"}
        for bv in f.iterfind("BoundaryValue"):
            v = _float(bv.get("value"))
            if bv.get("boundary") in names and v is not None:
                sides[names[bv.get("boundary", "")]] = v
        substrates.append({"name": ir_name, "diffusion_coefficient": (d or 0.0) * s * s / m,
                           "decay_rate": decay / m, "initial_value": max(initial or 0.0, 0.0),
                           "boundary": {"kind": "no_flux", "sides": sides}})  # fmt: skip
        rep.add(
            f"substrates[{ir_name}]",
            "exact" if d is not None else "approximated",
            "diffusion, decay and boundary values",
        )
    doc["substrates"] = substrates

    lattice_names = {"size.x": float(nx), "size.y": float(ny), "size.z": float(max(nz, 1))}
    for c in root.iterfind("Global/Constant"):
        value = _float(c.get("value"))
        if c.get("symbol") and value is not None:
            lattice_names[c.get("symbol", "")] = value

    def vec(text: str) -> list[float]:
        return [_arith(v, lattice_names) for v in text.split(",")]

    placements = []
    for pop in root.iterfind("CellPopulations/Population"):
        t = pop.get("type", "")
        for init in pop:
            if not isinstance(init.tag, str):
                continue
            dims_el = init.find("Dimensions")
            try:
                count = round(_arith(init.get("number-of-cells", "0"), lattice_names))
                if init.tag == "InitCircle" and dims_el is not None:
                    xy = vec(dims_el.get("center", "0, 0, 0"))
                    radius = _arith(dims_el.get("radius", "0"), lattice_names)
                    placements.append({"kind": "disc", "cell_type": t, "count": count,
                                       "centre": [xy[0] * s, xy[1] * s], "radius": radius * s})  # fmt: skip
                    continue
                if init.tag == "InitRectangle" and dims_el is not None:
                    o, wh = vec(dims_el.get("origin", "0, 0, 0")), vec(dims_el.get("size", "0, 0, 0"))
                    placements.append({"kind": "rectangle", "cell_type": t, "count": count,
                                       "centre": [(o[0] + wh[0] / 2) * s, (o[1] + wh[1] / 2) * s],
                                       "size": [wh[0] * s, wh[1] * s]})  # fmt: skip
                    continue
                note = "this initialiser has no ABM-IR reading yet"
            except ValueError as exc:
                note = str(exc)
            rep.add(f"initial_conditions[{t}].{init.tag}", "dropped", note)
    doc["initial_conditions"] = {"placements": placements}
    rep.add("initial_conditions", "approximated", "cell counts in circles and rectangles, placed at random")

    stop = mm.stop_time()
    doc["experiment"] = {"max_time": stop * m, "dt_diffusion": m, "dt_mechanics": m, "dt_phenotype": m,
                         "save_interval": max(m, stop * m / 10)}  # fmt: skip
    rep.add("experiment", "exact", f"{stop:g} time units")
    if root.find("Global/Event") is not None:
        rep.add("events", "dropped", "global Morpheus events are not translated")
    doc["provenance"] = {
        "derived_from": {"engine": "morpheus", "path": str(mm.path), "sha256": content_digest(mm.path)},
        "created_with": "ABMfact",
    }
    return model_from_dict(doc), rep
