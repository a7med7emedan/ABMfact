"""Translate ABM-IR models to CompuCell3D projects (tier T1), with a fidelity report.

ABM-IR describes centre-based cells in micron and minutes; CompuCell3D is a cellular Potts model on a lattice
of sites, advanced in Monte Carlo steps (MCS). Every quantity is converted with two stated scales,
``site_length`` (micron per site) and ``mcs_minutes`` (minutes per MCS), and the fidelity report names both.

The exported project has three parts:

* a CC3DML model for what the cellular Potts model does natively: the lattice, cell types, the volume
  constraint, contact energies from adhesion, and diffusion with decay and boundary values;
* ``abmfact_model.json``, the rest of the model converted to lattice units;
* a generic steppable module that runs it: placements, the live cycle, death and removal, Hill-type rules,
  persistent and chemotactic motility, secretion and uptake, attack and damage, and timed events.

What has no exact counterpart is marked ``approximated`` with a note, never converted silently: contact
energies from adhesion strengths, the volume stiffness, motility strength, pressure (read as the fraction of a
cell's boundary in contact with other cells), and diffusion coefficients too large for the explicit solver.
"""

from __future__ import annotations

import functools
import hashlib
import json
import math
import re
import shutil
import xml.etree.ElementTree as ET
from importlib import resources
from pathlib import Path
from typing import Any

from ...digest import content_bytes
from ...ir import IRModel
from ...records import FidelityReport

#: Contact energy between a cell and the medium; cells adhere to each other when J(a, b) < 2 * J_MEDIUM.
J_MEDIUM = 10.0
#: The PhysiCell default adhesion strength, which maps to J(a, b) = J_MEDIUM.
ADHESION_REF = 0.4
#: Volume constraint stiffness; cellular Potts only.
LAMBDA_VOLUME = 2.0
#: Temperature of the Metropolis dynamics.
TEMPERATURE = 10.0
#: External-potential strength per site per MCS of requested speed.
MOTILITY_LAMBDA = 50.0
#: Largest diffusion coefficient, in sites squared per MCS, run without capping; the explicit solver
#: substeps automatically, so its cost grows with the coefficient.
MAX_DIFFUSION = 200.0

RATE_BEHAVIOURS = {"cycle entry", "exit from cycle phase 0", "apoptosis", "necrosis"}
STEPPABLES = "abmfact_ir_steppables.py"


def cc3d_name(name: str) -> str:
    """A CompuCell3D type name for an ABM-IR name: non-word characters become underscores."""
    s = re.sub(r"\W+", "_", name).strip("_") or "type"
    return s if not s[0].isdigit() else f"t_{s}"


@functools.cache
def exporter_fingerprint() -> str:
    """A hash of this module and the steppable template, so a changed exporter never reuses old exports."""
    h = hashlib.sha256(content_bytes(Path(__file__)))
    h.update(_steppables_source().encode())
    return h.hexdigest()


def _steppables_source() -> str:
    return (resources.files(__package__) / "ir_steppables.py.in").read_text(encoding="utf-8")


def _disc_area(volume: float, dims: int) -> float:
    """Sites' worth of area (2D) or volume (3D) for a cell of ``volume`` cubic micron."""
    if dims == 3:
        return volume
    r = (3.0 * volume / (4.0 * math.pi)) ** (1.0 / 3.0)
    return math.pi * r * r


def export_project(
    model: IRModel, out: str | Path, site_length: float = 2.0, mcs_minutes: float = 1.0
) -> FidelityReport:
    """Write ``model`` as a CompuCell3D project in ``out`` and return what was kept, approximated or dropped."""
    out = Path(out)
    s, m = float(site_length), float(mcs_minutes)
    rep = FidelityReport("abm-ir", f"compucell3d:{out.name}", tier="T1")
    rep.add("units", "approximated", f"one lattice site is {s:g} micron and one Monte Carlo step {m:g} minutes")
    if out.exists():
        shutil.rmtree(out)
    sim = out / "Simulation"
    sim.mkdir(parents=True)
    dims = model.space.dimensions
    b = model.space.bounds
    nx = max(1, round((b.x_max - b.x_min) / s))
    ny = max(1, round((b.y_max - b.y_min) / s))
    nz = max(1, round((b.z_max - b.z_min) / s)) if dims == 3 else 1
    site_area = s**dims

    root = ET.Element("CompuCell3D", version="4.10.0")
    potts = ET.SubElement(root, "Potts")
    ET.SubElement(potts, "Dimensions", x=str(nx), y=str(ny), z=str(nz))
    steps = max(1, round(model.experiment.max_time / m))
    ET.SubElement(potts, "Steps").text = str(steps)
    ET.SubElement(potts, "Temperature").text = repr(TEMPERATURE)
    ET.SubElement(potts, "NeighborOrder").text = "2"
    if model.space.boundary == "periodic":
        for axis in ("x", "y") + (("z",) if dims == 3 else ()):
            ET.SubElement(potts, f"Boundary_{axis}").text = "Periodic"
    rep.add("space", "exact", f"{nx} x {ny}{f' x {nz}' if dims == 3 else ''} sites, {model.space.boundary} edges")

    names = [cc3d_name(c.name) for c in model.cell_types]
    if names != [c.name for c in model.cell_types]:
        rep.add("cell_types[*].name", "approximated", "renamed to identifiers, as CompuCell3D type names must be")
    ct_plugin = ET.SubElement(root, "Plugin", Name="CellType")
    ET.SubElement(ct_plugin, "CellType", TypeId="0", TypeName="Medium")
    for i, n in enumerate(names, 1):
        ET.SubElement(ct_plugin, "CellType", TypeId=str(i), TypeName=n)
    for plugin in ("Volume", "CenterOfMass", "NeighborTracker", "ExternalPotential"):
        ET.SubElement(root, "Plugin", Name=plugin)
    rep.add("cell_types[*].volume", "approximated",
            f"a target {'area' if dims == 2 else 'volume'} in sites with volume stiffness {LAMBDA_VOLUME:g}, which "
            "ABM-IR does not have")  # fmt: skip

    contact = ET.SubElement(root, "Plugin", Name="Contact")
    ET.SubElement(contact, "Energy", Type1="Medium", Type2="Medium").text = "0.0"
    for n in names:
        ET.SubElement(contact, "Energy", Type1="Medium", Type2=n).text = repr(J_MEDIUM)
    for i, a in enumerate(model.cell_types):
        for bt in model.cell_types[i:]:
            eff = math.sqrt(
                a.mechanics.adhesion_strength * a.mechanics.adhesion_affinities.get(bt.name, 1.0)
                * bt.mechanics.adhesion_strength * bt.mechanics.adhesion_affinities.get(a.name, 1.0)
            )  # fmt: skip
            j = max(2.0, 2.0 * J_MEDIUM - J_MEDIUM * eff / ADHESION_REF)
            ET.SubElement(contact, "Energy", Type1=cc3d_name(a.name), Type2=cc3d_name(bt.name)).text = repr(round(j, 6))
    ET.SubElement(contact, "NeighborOrder").text = "2"
    note = (
        f"contact energies J(a,b) = 2*{J_MEDIUM:g} - {J_MEDIUM:g}*e/{ADHESION_REF:g} (at least 2), with e "
        "the geometric mean of the two adhesion strengths times affinities; repulsion is the volume constraint"
    )
    rep.add("cell_types[*].mechanics", "approximated", note)

    fields = [x.name for x in model.substrates]
    slowdown: dict[str, float] = {}
    if model.substrates:
        # The field secretor the generated steppable uses needs the Secretion plugin declared.
        ET.SubElement(root, "Plugin", Name="Secretion")
        solver = ET.SubElement(root, "Steppable", Type="DiffusionSolverFE")
        for sub in model.substrates:
            df = ET.SubElement(solver, "DiffusionField", Name=sub.name)
            dd = ET.SubElement(df, "DiffusionData")
            ET.SubElement(dd, "FieldName").text = sub.name
            # Fast diffusion is capped for speed. The field's reactions are slowed by the same factor, which keeps
            # the steady profile (its decay length sqrt(D/k)) and slows only settling.
            d_sites = sub.diffusion_coefficient * m / (s * s)
            slow = max(1.0, d_sites / MAX_DIFFUSION)
            if slow > 1:
                rep.add(f"substrates[{sub.name}].diffusion_coefficient", "approximated",
                        f"{d_sites:.4g} sites^2/MCS capped at {MAX_DIFFUSION:g} ({slow:.3g} times slower), and the "
                        "field's secretion, uptake and decay slowed by the same factor, so its steady profile is kept "
                        "and only its settling is slower; a larger site_length keeps more of the speed")  # fmt: skip
                d_sites = MAX_DIFFUSION
            slowdown[sub.name] = slow
            ET.SubElement(dd, "GlobalDiffusionConstant").text = repr(d_sites)
            ET.SubElement(dd, "GlobalDecayConstant").text = repr(sub.decay_rate * m / slow)
            ET.SubElement(dd, "InitialConcentrationExpression").text = repr(float(sub.initial_value))
            bc = ET.SubElement(df, "BoundaryConditions")
            for axis, lo, hi in (("X", "x_min", "x_max"), ("Y", "y_min", "y_max")):
                plane = ET.SubElement(bc, "Plane", Axis=axis)
                for pos, side in (("Min", lo), ("Max", hi)):
                    if model.space.boundary == "periodic":
                        ET.SubElement(plane, "Periodic")
                        break
                    value = sub.boundary.value_at(side)
                    if value is None:
                        ET.SubElement(plane, "ConstantDerivative", PlanePosition=pos, Value="0.0")
                    else:
                        ET.SubElement(plane, "ConstantValue", PlanePosition=pos, Value=repr(float(value)))
            rep.add(f"substrates[{sub.name}]", "exact", "diffusion, decay, initial value and boundary values")

    types = []
    for ct in model.cell_types:
        base: dict[str, float] = {
            "cycle entry": (ct.cycle.rates or [ct.cycle.division_rate])[0] * m,
            "apoptosis": ct.death.apoptosis_rate * m,
            "necrosis": ct.death.necrosis_rate * m,
            "migration speed": ct.motility.speed * m / s,
            "migration bias": ct.motility.migration_bias,
            "migration persistence time": ct.motility.persistence_time / m,
        }
        for f in fields:
            sec = next((x for x in ct.secretion if x.substrate == f), None)
            base[f"{f} secretion"] = (sec.secretion_rate if sec else 0.0) * m
            base[f"{f} secretion target"] = sec.target if sec else 1.0
            base[f"{f} uptake"] = (sec.uptake_rate if sec else 0.0) * m
            base[f"{f} export"] = (sec.net_export_rate if sec else 0.0) * m
        rules: dict[str, list[dict[str, Any]]] = {}
        for r in model.rules:
            if r.cell_type != ct.name:
                continue
            behaviour = "cycle entry" if r.behaviour == "exit from cycle phase 0" else r.behaviour
            sat = r.saturation_value
            if behaviour in RATE_BEHAVIOURS or behaviour.endswith((" secretion", " uptake", " export")):
                sat *= m
            elif behaviour == "migration speed":
                sat *= m / s
            elif behaviour == "migration persistence time":
                sat /= m
            rules.setdefault(behaviour, []).append({"signal": r.signal, "direction": r.direction, "saturation": sat,
                                                    "half_max": r.half_max, "hill": r.hill_power,
                                                    "apply_to_dead": r.apply_to_dead})  # fmt: skip
            base.setdefault(behaviour, 0.0)
        chemo = ct.motility.chemotaxis
        types.append({
            "name": ct.name,
            "cc3d": cc3d_name(ct.name),
            "target_volume": max(1.0, round(_disc_area(ct.volume, dims) / site_area)),
            "lambda_volume": LAMBDA_VOLUME,
            "base": base,
            "rules": rules,
            "apoptosis_duration": ct.death.apoptosis_duration / m,
            "necrosis_duration": ct.death.necrosis_duration / m,
            "chemotaxis": {"field": chemo.substrate, "direction": chemo.direction} if chemo else None,
            "attack_rates": {cc3d_name(k): v * m for k, v in ct.interactions.attack_rates.items() if v > 0},
            "damage_rate": ct.interactions.damage_rate * m,
            "attack_duration": ct.interactions.attack_duration / m,
            "immunogenicities": {cc3d_name(k): v for k, v in ct.interactions.immunogenicities.items()},
            "damage_growth": ct.integrity.damage_rate * m,
            "damage_repair": ct.integrity.damage_repair_rate * m,
        })  # fmt: skip
        base_path = f"cell_types[{ct.name}]"
        live = ct.cycle.model == "live"
        rep.add(f"{base_path}.cycle", "exact" if live else "approximated",
                "the live cycle" if live else "run as the live cycle with its division rate")  # fmt: skip
        rep.add(f"{base_path}.death", "approximated",
                "dead cells keep their size until their clearance time, then shrink away")  # fmt: skip
        if ct.motility.speed > 0:
            rep.add(f"{base_path}.motility", "approximated",
                    f"a persistent walk driven by an external potential of strength {MOTILITY_LAMBDA:g} x speed; "
                    "the speed reached depends on the temperature and the volume constraint")  # fmt: skip
        if ct.interactions.attack_rates:
            rep.add(f"{base_path}.interactions", "exact", "attack, damage and attack duration, as in PhysiCell 1.14.2")
        it = ct.interactions
        if it.phagocytosis_rates or it.fusion_rates or it.dead_phagocytosis_rate > 0:
            rep.add(f"{base_path}.interactions", "dropped", "phagocytosis and fusion are not exported")
        if any(r > 0 for r in ct.transformations.values()):
            rep.add(f"{base_path}.transformations", "dropped", "transformations are not exported")
        if ct.secretion:
            rep.add(f"{base_path}.secretion", "approximated",
                    "secretion towards the target and uptake at the cell's centre value, through CompuCell3D's field "
                    "secretor")  # fmt: skip
    if any(r.signal == "pressure" for r in model.rules):
        rep.add("rules.pressure", "approximated",
                "cellular Potts has no pressure; the signal is the fraction of the cell's boundary touching other "
                "cells")  # fmt: skip
    if model.rules:
        rep.add("rules", "exact", "Hill-type rules combined as in PhysiCell 1.14.2")

    placements = []
    for p in model.initial_conditions.placements:
        if p.kind == "positions_csv" or p.cell_type is None:
            rep.add("initial_conditions.placements", "dropped", "positions from a CSV file are not exported")
            continue
        radius = p.radius or 0.0
        size = p.size or (0.0, 0.0)
        count = p.count if p.count is not None else 0
        if p.count is None and p.density is not None:
            if p.kind == "disc":
                area = math.pi * radius**2
            elif p.kind == "rectangle":
                area = size[0] * size[1]
            else:
                area = (b.x_max - b.x_min) * (b.y_max - b.y_min)
            count = round(p.density * area)
        placements.append({
            "kind": p.kind, "cell_type": cc3d_name(p.cell_type), "count": count,
            "centre": [(p.centre[0] - b.x_min) / s, (p.centre[1] - b.y_min) / s],
            "radius": radius / s,
            "size": [x / s for x in size],
            "positions": [[(x - b.x_min) / s, (y - b.y_min) / s] for x, y in (p.positions or [])],
        })  # fmt: skip
    rep.add("initial_conditions.placements", "approximated",
            "each cell starts on the free sites of a square of its target area, or the nearest free site, and "
            "grows to size; ABM-IR cells may overlap at the start, lattice cells cannot")  # fmt: skip

    events = []
    for k, ev in enumerate(model.events):
        if ev.action == "set_parameter":
            rep.add(f"events[{k}]", "dropped", "parameter changes during a run are not exported")
            continue
        target = cc3d_name(ev.target) if ev.action == "add_cells" else ev.target
        events.append({"time": ev.time / m, "until": None if ev.until is None else ev.until / m,
                       "action": ev.action, "target": target, "value": ev.value})  # fmt: skip
        note = (
            "the value is held on every edge site while the event lasts"
            if ev.action == "set_substrate_boundary"
            else ""
        )
        rep.add(f"events[{k}]", "approximated" if note else "exact", note)
    if model.intracellular:
        rep.add("intracellular", "dropped", "intracellular models are not exported to CompuCell3D yet")
    for k, x in enumerate(model.native_extensions):
        rep.add(f"native_extensions[{k}]", "dropped", f"{x.engine} code does not run in CompuCell3D")

    spec = {
        "xml": "abmfact_model.xml",
        "site_length": s,
        "mcs_minutes": m,
        "site_volume": site_area,
        "phenotype_every": max(1, round(model.experiment.dt_phenotype / m)),
        "motility_lambda": MOTILITY_LAMBDA,
        "fields": fields,
        "field_slowdown": slowdown,
        "types": types,
        "placements": placements,
        "events": events,
    }
    (sim / "abmfact_model.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
    ET.ElementTree(root).write(sim / "abmfact_model.xml", encoding="unicode")
    (sim / STEPPABLES).write_text(_steppables_source(), encoding="utf-8")
    (sim / "abmfact_main.py").write_text(
        "from cc3d import CompuCellSetup\n"
        f"from .{STEPPABLES[:-3]} import IRBehaviour, IRDivision\n\n"
        "CompuCellSetup.register_steppable(steppable=IRBehaviour())\n"
        "CompuCellSetup.register_steppable(steppable=IRDivision())\n"
        "CompuCellSetup.run()\n",
        encoding="utf-8",
    )
    (out / f"{model.name}.cc3d").write_text(
        '<Simulation version="4.10.0">\n'
        '   <XMLScript Type="XMLScript">Simulation/abmfact_model.xml</XMLScript>\n'
        '   <PythonScript Type="PythonScript">Simulation/abmfact_main.py</PythonScript>\n'
        f'   <Resource Type="Python">Simulation/{STEPPABLES}</Resource>\n'
        '   <Resource Type="Other">Simulation/abmfact_model.json</Resource>\n'
        "</Simulation>\n",
        encoding="utf-8",
    )
    rep.add("experiment", "exact", f"{steps} MCS for {model.experiment.max_time:g} minutes; the phenotype every "
            f"{spec['phenotype_every']} MCS")  # fmt: skip
    return rep


# -- import ------------------------------------------------------------------------------------------------


def _text(el: ET.Element | None, *paths: str, default: float = 0.0) -> float:
    for path in paths:
        t = el.findtext(path) if el is not None else None
        if t is not None and t.strip():
            return float(t)
    return default


def import_project(
    folder: str | Path, site_length: float = 1.0, mcs_minutes: float = 1.0
) -> tuple[IRModel, FidelityReport]:
    """Read a CompuCell3D project into ABM-IR, with what was kept, approximated, dropped or left native.

    Lattice sites become ``site_length`` micron and Monte Carlo steps ``mcs_minutes`` minutes; with the defaults
    the model stays in lattice units. Only what the CC3DML declares is read; the project's Python code is a
    native extension, run by CompuCell3D and never translated.
    """
    from ...digest import content_digest
    from ...ir import model_from_dict
    from .project import CC3DProject

    project = CC3DProject.read(folder)
    s, m = float(site_length), float(mcs_minutes)
    rep = FidelityReport(f"compucell3d:{project.folder.name}", "abm-ir", tier="T1")
    rep.add("units", "approximated" if (s, m) != (1.0, 1.0) else "exact",
            f"one lattice site is {s:g} micron and one Monte Carlo step {m:g} minutes")  # fmt: skip
    root = project.root
    if root is None:
        raise ValueError(f"{project.cc3d_file.name} has no CC3DML model; only XML-based projects import")
    nx, ny, nz = project.dimensions()
    dims = 2 if nz == 1 else 3
    potts = root.find("Potts")
    periodic = potts is not None and (potts.findtext("Boundary_x") or "").strip() == "Periodic"
    doc: dict[str, Any] = {
        "schema_version": "0.1",
        "name": cc3d_name(project.cc3d_file.stem),
        "description": f"Imported from the CompuCell3D project {project.cc3d_file.name}.",
        "space": {
            "dimensions": dims,
            "bounds": {
                "x_min": 0,
                "x_max": nx * s,
                "y_min": 0,
                "y_max": ny * s,
                "z_min": 0,
                "z_max": (nz if dims == 3 else 1) * s,
            },
            "voxel": {"dx": s, "dy": s, "dz": s},
            "boundary": "periodic" if periodic else "closed",
        },
    }
    rep.add("space", "exact", f"{nx} x {ny}{f' x {nz}' if dims == 3 else ''} sites")

    types = project.cell_types()
    volumes: dict[str, float] = {}
    for vp in root.iterfind("Plugin[@Name='Volume']/VolumeEnergyParameters"):
        volumes[vp.get("CellType", "")] = float(vp.get("TargetVolume", "25"))
    # A project ABMfact exported sets volumes per cell from Python; its converted model holds them.
    exported_model = project.xml_path.parent / "abmfact_model.json" if project.xml_path else None
    if exported_model is not None and exported_model.is_file():
        for t in json.loads(exported_model.read_text(encoding="utf-8")).get("types", []):
            volumes.setdefault(t.get("cc3d", t.get("name", "")), float(t["target_volume"]))
    energies: dict[frozenset[str], float] = {}
    for e in root.iterfind("Plugin[@Name='Contact']/Energy"):
        energies[frozenset((e.get("Type1", ""), e.get("Type2", "")))] = float(e.text or 0)
    cell_types = []
    for t in types:
        area = volumes.get(t, 25.0) * s**dims
        volume = area if dims == 3 else 4.0 / 3.0 * math.pi * (area / math.pi) ** 1.5
        j_medium = energies.get(frozenset(("Medium", t)), J_MEDIUM)
        strength = {}
        for other in types:
            j = energies.get(frozenset((t, other)))
            if j is not None:
                strength[other] = max(0.0, (2.0 * j_medium - j) / j_medium * ADHESION_REF)
        own = strength.get(t, ADHESION_REF)
        cell_types.append({
            "name": t,
            "volume": volume,
            "cycle": {"model": "live", "rates": [0.0]},
            "mechanics": {"adhesion_strength": own,
                          "adhesion_affinities": {o: (v / own if own > 0 else 0.0) for o, v in strength.items()}},
        })  # fmt: skip
    doc["cell_types"] = cell_types
    rep.add("cell_types[*].volume", "approximated", "target volumes in sites, read as the volume of a sphere")
    if energies:
        rep.add("cell_types[*].mechanics", "approximated",
                "adhesion strengths and affinities from contact energies, inverting the export mapping")  # fmt: skip
    rep.add("cell_types[*].cycle", "approximated", "no division in CC3DML; any division is Python code")

    substrates = []
    for df in root.iterfind("Steppable/DiffusionField"):
        name = df.get("Name") or (df.findtext("DiffusionData/FieldName") or "").strip()
        dd = df.find("DiffusionData")
        d = _text(dd, "GlobalDiffusionConstant", "DiffusionConstant")
        decay = _text(dd, "GlobalDecayConstant", "DecayConstant")
        initial = 0.0
        expr = (dd.findtext("InitialConcentrationExpression") if dd is not None else None) or ""
        try:
            initial = float(expr) if expr.strip() else 0.0
        except ValueError:
            rep.add(f"substrates[{name}].initial_value", "approximated", f"the expression {expr!r} is read as 0")
        if dd is not None and dd.find("ConcentrationFileName") is not None:
            rep.add(f"substrates[{name}].initial_value", "dropped", "an initial field read from a file")
        sides: dict[str, float | None] = {}
        for plane in df.iterfind("BoundaryConditions/Plane"):
            axis = plane.get("Axis", "X").lower()
            for cv in plane.iterfind("ConstantValue"):
                sides[f"{axis}_{'min' if cv.get('PlanePosition') == 'Min' else 'max'}"] = float(cv.get("Value", "0"))
        substrates.append({"name": name, "diffusion_coefficient": d * s * s / m, "decay_rate": decay / m,
                           "initial_value": max(initial, 0.0),
                           "boundary": {"kind": "no_flux", "sides": sides}})  # fmt: skip
        rep.add(f"substrates[{name}]", "exact", "diffusion, decay and boundary values")
    doc["substrates"] = substrates

    placements = []
    for init in root.iterfind("Steppable"):
        kind = init.get("Type", "")
        if kind not in ("BlobInitializer", "UniformInitializer"):
            continue
        for region in init.iterfind("Region"):
            width = _text(region, "Width", default=5.0)
            names = [n.strip() for n in (region.findtext("Types") or "").split(",") if n.strip() in types]
            if not names:
                continue
            if kind == "BlobInitializer":
                c = region.find("Center")
                cx = float(c.get("x", "0")) if c is not None else 0.0
                cy = float(c.get("y", "0")) if c is not None else 0.0
                r = _text(region, "Radius")
                n = max(1, round(math.pi * r * r / (width * width)))
                for k, name in enumerate(names):
                    share = n // len(names) + (1 if k < n % len(names) else 0)
                    placements.append({"kind": "disc", "cell_type": name, "count": share, "centre": [cx * s, cy * s],
                                       "radius": r * s})  # fmt: skip
            else:
                b0, b1 = region.find("BoxMin"), region.find("BoxMax")
                if b0 is None or b1 is None:
                    continue
                x0, y0 = float(b0.get("x", "0")), float(b0.get("y", "0"))
                x1, y1 = float(b1.get("x", "0")), float(b1.get("y", "0"))
                n = max(1, round((x1 - x0) * (y1 - y0) / (width * width)))
                for k, name in enumerate(names):
                    share = n // len(names) + (1 if k < n % len(names) else 0)
                    placements.append({"kind": "rectangle", "cell_type": name, "count": share,
                                       "centre": [(x0 + x1) / 2 * s, (y0 + y1) / 2 * s],
                                       "size": [(x1 - x0) * s, (y1 - y0) * s]})  # fmt: skip
        rep.add(f"initial_conditions[{kind}]", "approximated",
                "cells of the region's width as a count, mixed at random instead of in a grid")  # fmt: skip
    doc["initial_conditions"] = {"placements": placements}

    steps = project.steps()
    doc["experiment"] = {"max_time": steps * m, "dt_diffusion": m, "dt_mechanics": m, "dt_phenotype": m,
                         "save_interval": max(m, steps * m / 10)}  # fmt: skip
    rep.add("experiment", "exact", f"{steps} MCS")

    known = {"CellType", "Volume", "Contact", "CenterOfMass", "NeighborTracker"}
    for plugin in root.iterfind("Plugin"):
        name = plugin.get("Name", "")
        if name not in known:
            rep.add(f"plugins[{name}]", "dropped", "this CompuCell3D plugin has no ABM-IR counterpart yet")
    natives = []
    files = [p.relative_to(project.folder).as_posix() for p in project.python_files()]
    if files:
        natives.append({"engine": "compucell3d", "kind": "custom_function", "files": files,
                        "description": "the project's Python main script and steppables",
                        "affects": [f"cell_types[{t}]" for t in types]})  # fmt: skip
        rep.add("native_extensions[python]", "native", "the project's Python code runs in CompuCell3D only")
    doc["native_extensions"] = natives
    doc["provenance"] = {
        "derived_from": {"engine": "compucell3d", "path": str(project.cc3d_file),
                         "sha256": content_digest(project.xml_path) if project.xml_path else None},
        "created_with": "ABMfact",
    }  # fmt: skip
    return model_from_dict(doc), rep
