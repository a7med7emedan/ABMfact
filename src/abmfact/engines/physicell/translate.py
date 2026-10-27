"""Translate between PhysiCell projects and ABM-IR, with a fidelity report each way (tier T1).

Import reads the declarative part of a project: the settings XML, the rules CSV and the initial cell
positions. Custom C++ in ``custom_modules`` is listed as a native extension and never translated.
Export writes a settings XML, a rules CSV and a cells CSV onto PhysiCell's own template project (bundled,
BSD-3), or onto a project you give. Every field is reported as exact, approximated, dropped or native.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import math
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np

from ... import __version__
from ...digest import content_bytes, content_digest
from ...ir import IRModel, model_from_dict
from ...ir.io import read_cells_csv
from ...records import FidelityReport
from .build import PROJECT_DIRS, PROJECT_FILES
from .engine import SETTINGS, default_observables
from .rules import RuleRow, read_rules_csv, write_rules_csv
from .settings import SettingsXML

TEMPLATE = Path(__file__).parent / "template"

#: SHA-256 of the 1.14.2 template's custom code, to say whether a project's custom code is the template's.
TEMPLATE_CUSTOM = {
    "custom.cpp": "2f70de6e7087126585b724ab9e5bdc1cbdcdd6e18e17e728cf5fa20edf07780d",
    "custom.h": "fc4f19caec74ecfc175493e4a8d1ca5366e73f26cc3e52515195e9c9c4d5f4cd",
}

CYCLE_CODES = {
    0: "ki67_advanced",
    1: "ki67_basic",
    2: "flow_cytometry",
    5: "live",
    6: "flow_cytometry_separated",
    7: "cycling_quiescent",
}
CYCLE_NAMES = {
    "ki67_advanced": "Ki67 (advanced)",
    "ki67_basic": "Ki67 (basic)",
    "flow_cytometry": "Flow cytometry model (basic)",
    "live": "live",
    "flow_cytometry_separated": "Flow cytometry model (separated)",
    "cycling_quiescent": "Cycling-Quiescent model",
}
#: PhysiCell runs mechanics at most once per diffusion step, and Cell::update_position fixes its step from the
#: first call, so a diffusion step longer than the mechanics step slows every cell down.
DT_ORDER_NOTE = (
    "dt_diffusion lowered to dt_mechanics: PhysiCell runs mechanics at most once per diffusion step with a "
    "fixed dt_mechanics, so a longer diffusion step would slow every cell down"
)
#: Largest dt_diffusion * D / dx^2 an export keeps. BioFVM's diffusion solver splits the step by axis and is far
#: from converged above it: in the Gate 3 spheroid (D 1e5, dx 20, dt 0.1, so 25) a field with no cells settled at
#: 33.0 at its centre, where the converged value is 36.4; at 0.25 it was 36.3 and the spheroid matched the reference.
MAX_DIFFUSION_NUMBER = 0.25
SIDES = {"xmin": "x_min", "xmax": "x_max", "ymin": "y_min", "ymax": "y_max", "zmin": "z_min", "zmax": "z_max"}


@functools.cache
def exporter_fingerprint() -> str:
    """A hash of this module's code and the bundled template, so a changed exporter never reuses old exports."""
    h = hashlib.sha256(content_bytes(Path(__file__)))
    for f in sorted(p for p in TEMPLATE.rglob("*") if p.is_file()):
        h.update(f.relative_to(TEMPLATE).as_posix().encode())
        h.update(content_bytes(f))
    return h.hexdigest()


#: The template's only substrate, kept (inert) when an ABM-IR model has none.
INERT_SUBSTRATE = "substrate"


def _f(el: ET.Element | None, path: str, default: float = 0.0) -> float:
    if el is None:
        return default
    text = el.findtext(path)
    return default if text is None or not text.strip() else float(text)


def _true(text: str | None) -> bool:
    return (text or "").strip().lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------------------------------------
# Import


def _import_substrate(v: ET.Element, rep: FidelityReport, is_2d: bool) -> dict[str, Any]:
    name = v.get("name", "")
    base = f"substrates[{name}]"
    dbc = v.find("Dirichlet_boundary_condition")
    global_on = dbc is not None and _true(dbc.get("enabled"))
    value = _f(v, "Dirichlet_boundary_condition")
    sides: dict[str, float | None] = {}
    for bv in v.iterfind("Dirichlet_options/boundary_value"):
        side = SIDES.get(bv.get("ID", ""))
        if side is None or (is_2d and side.startswith("z")):
            continue
        on = _true(bv.get("enabled"))
        side_value = float(bv.text or 0.0)
        if global_on and (not on or side_value != value):
            sides[side] = side_value if on else None
        elif not global_on and on:
            rep.add(
                f"{base}.boundary.sides.{side}",
                "approximated",
                "per-side Dirichlet value with the global condition disabled; read as no-flux",
            )
    rep.add(f"{base}", "exact", "diffusion, decay, initial value and boundary values")
    return {
        "name": name,
        "units": v.get("units", "dimensionless"),
        "diffusion_coefficient": _f(v, "physical_parameter_set/diffusion_coefficient"),
        "decay_rate": _f(v, "physical_parameter_set/decay_rate"),
        "initial_value": _f(v, "initial_condition"),
        "boundary": {
            "kind": "dirichlet" if global_on else "no_flux",
            "value": value if global_on else 0.0,
            "sides": sides,
        },
    }


def _import_cycle(ph: ET.Element, base: str, rep: FidelityReport) -> dict[str, Any]:
    cyc = ph.find("cycle")
    if cyc is None:
        rep.add(f"{base}.cycle", "approximated", "no cycle element; read as a live cycle that never divides")
        return {"model": "live", "rates": [0.0]}
    code = int(cyc.get("code", "5"))
    model = CYCLE_CODES.get(code, "custom")
    rates = [float(r.text or 0) for r in cyc.iterfind("phase_transition_rates/rate")]
    durations = [float(d.text or 0) for d in cyc.iterfind("phase_durations/duration")]
    fixed = [_true(e.get("fixed_duration")) for e in [*cyc.iterfind(".//rate"), *cyc.iterfind(".//duration")]]
    if model == "custom":
        rep.add(f"{base}.cycle", "approximated", f"cycle code {code} has no ABM-IR model; read as custom")
    elif any(fixed):
        rep.add(
            f"{base}.cycle",
            "approximated",
            "fixed-duration phases are not distinguished from exponential ones in ABM-IR",
        )
    else:
        rep.add(f"{base}.cycle", "exact", model)
    if durations and not rates:
        if any(d <= 0 for d in durations):
            return {"model": model, "rates": [1.0 / d if d > 0 else 0.0 for d in durations]}
        return {"model": model, "durations": durations}
    return {"model": model, "rates": rates or [0.0]}


def _import_death(ph: ET.Element, base: str, rep: FidelityReport) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for m in ph.iterfind("death/model"):
        code = int(m.get("code", "0"))
        durations = [float(d.text or 0) for d in m.iterfind("phase_durations/duration")]
        if code == 100:
            out["apoptosis_rate"] = _f(m, "death_rate")
            if durations and durations[0] > 0:
                out["apoptosis_duration"] = durations[0]
        elif code == 101:
            out["necrosis_rate"] = _f(m, "death_rate")
            if durations and sum(durations) > 0:
                out["necrosis_duration"] = sum(durations)
    rep.add(
        f"{base}.death", "approximated", "rates and clearance times kept; volume changes during death are not in ABM-IR"
    )
    return out


def _import_cell_type(cd: ET.Element, rep: FidelityReport, subs: list[str]) -> dict[str, Any]:
    name = cd.get("name", "")
    base = f"cell_types[{name}]"
    ph = cd.find("phenotype")
    if ph is None:
        rep.add(base, "approximated", "no phenotype element; defaults used")
        return {"name": name}
    ct: dict[str, Any] = {"name": name, "volume": _f(ph, "volume/total", 2494.0)}
    rep.add(f"{base}.volume", "approximated", "total volume kept; volume growth and fluid dynamics are not in ABM-IR")
    ct["cycle"] = _import_cycle(ph, base, rep)
    ct["death"] = _import_death(ph, base, rep)
    mech = ph.find("mechanics")
    ct["mechanics"] = {
        "adhesion_strength": _f(mech, "cell_cell_adhesion_strength", 0.4),
        "repulsion_strength": _f(mech, "cell_cell_repulsion_strength", 10.0),
        "max_adhesion_distance": _f(mech, "relative_maximum_adhesion_distance", 1.25),
        "adhesion_affinities": {
            a.get("name", ""): float(a.text or 1)
            for a in ph.iterfind("mechanics/cell_adhesion_affinities/cell_adhesion_affinity")
        },
    }
    rep.add(f"{base}.mechanics", "exact", "adhesion, repulsion, adhesion distance and affinities")
    if _f(mech, "attachment_rate") > 0 or any(_true(o.get("enabled")) for o in ph.iterfind("mechanics/options/*")):
        rep.add(f"{base}.mechanics.attachments", "dropped", "spring attachments and equilibrium-distance options")
    mot = ph.find("motility")
    enabled = _true(ph.findtext("motility/options/enabled"))
    ct["motility"] = {
        "speed": _f(mot, "speed") if enabled else 0.0,
        "persistence_time": max(_f(mot, "persistence_time", 1.0), 1e-9),
        "migration_bias": min(max(_f(mot, "migration_bias"), 0.0), 1.0),
    }
    if not enabled and _f(mot, "speed") > 0:
        rep.add(f"{base}.motility.speed", "approximated", "motility is disabled, so speed is read as 0")
    if enabled and _true(ph.findtext("motility/options/chemotaxis/enabled")):
        ct["motility"]["chemotaxis"] = {
            "substrate": ph.findtext("motility/options/chemotaxis/substrate", "").strip(),
            "direction": int(float(ph.findtext("motility/options/chemotaxis/direction", "1"))),
        }
    if _true(ph.findtext("motility/options/advanced_chemotaxis/enabled")):
        rep.add(f"{base}.motility.advanced_chemotaxis", "dropped", "advanced chemotaxis is not in ABM-IR")
    rep.add(f"{base}.motility", "exact", "speed, persistence, bias and chemotaxis")
    ct["secretion"] = [
        {
            "substrate": s.get("name", ""),
            "secretion_rate": _f(s, "secretion_rate"),
            "target": _f(s, "secretion_target", 1),
            "uptake_rate": _f(s, "uptake_rate"),
            "net_export_rate": _f(s, "net_export_rate"),
        }
        for s in ph.iterfind("secretion/substrate")
        if s.get("name", "") in subs
    ]
    rep.add(f"{base}.secretion", "exact")
    ci = ph.find("cell_interactions")
    dead = [
        _f(ci, k)
        for k in (
            "apoptotic_phagocytosis_rate",
            "necrotic_phagocytosis_rate",
            "other_dead_phagocytosis_rate",
            "dead_phagocytosis_rate",
        )
    ]
    ct["interactions"] = {
        "attack_rates": {
            a.get("name", ""): float(a.text or 0) for a in ph.iterfind("cell_interactions/attack_rates/*")
        },
        "phagocytosis_rates": {
            a.get("name", ""): float(a.text or 0) for a in ph.iterfind("cell_interactions/live_phagocytosis_rates/*")
        },
        "fusion_rates": {
            a.get("name", ""): float(a.text or 0) for a in ph.iterfind("cell_interactions/fusion_rates/*")
        },
        "damage_rate": _f(ci, "attack_damage_rate"),
        "attack_duration": max(_f(ci, "attack_duration", 0.1), 1e-15),
        "dead_phagocytosis_rate": max(dead),
    }
    ct["integrity"] = {
        "damage_rate": _f(ph, "cell_integrity/damage_rate"),
        "damage_repair_rate": _f(ph, "cell_integrity/damage_repair_rate"),
    }
    if len({d for d in dead if d > 0}) > 1:
        rep.add(
            f"{base}.interactions.dead_phagocytosis_rate",
            "approximated",
            "apoptotic, necrotic and other dead phagocytosis rates differ; the largest is kept",
        )
    rep.add(f"{base}.integrity", "exact", "damage and repair rates")
    ct["transformations"] = {
        t.get("name", ""): float(t.text or 0) for t in ph.iterfind("cell_transformations/transformation_rates/*")
    }
    rep.add(f"{base}.interactions", "exact", "attack, phagocytosis, fusion and transformation rates")
    ct["custom"] = {}
    for var in cd.iterfind("custom_data/*"):
        try:
            ct["custom"][var.tag] = float(var.text or 0)
        except ValueError:
            rep.add(f"{base}.custom.{var.tag}", "dropped", "custom data that is not a number")
    ipd = cd.find("initial_parameter_distributions")
    if ipd is not None and _true(ipd.get("enabled")):
        rep.add(f"{base}.initial_parameter_distributions", "dropped", "per-cell parameter distributions")
    return ct


def _sha(path: Path) -> str:
    """SHA-256 of a file with text line endings normalised, so a Windows checkout (CRLF) gives the same."""
    return content_digest(path)


def import_project(project: str | Path, out_dir: str | Path | None = None) -> tuple[IRModel, FidelityReport]:
    """Read a PhysiCell project into ABM-IR. Files the model needs (cells CSV, intracellular models) are
    copied to ``out_dir`` when given, so the model is self-contained there."""
    project = Path(project)
    settings_path = project / SETTINGS
    s = SettingsXML.read(settings_path)
    rep = FidelityReport(f"physicell:{project.name}", "abm-ir", tier="T1")
    is_2d = s.is_2d
    dom = {k: s.get_float(f"domain/{k}") for k in ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max")}
    space = {"dimensions": 2 if is_2d else 3, "bounds": dom, "voxel": s.voxel(), "boundary": "closed"}
    if not s.get_bool("options/virtual_wall_at_domain_edge", default=True):
        rep.add("space.boundary", "approximated", "no virtual wall: PhysiCell removes cells that leave; read as closed")
    else:
        rep.add("space", "exact", "domain, voxel size and walled boundary")
    subs_el = s.substrates()
    subs = [v.get("name", "") for v in subs_el]
    substrates = [_import_substrate(v, rep, is_2d) for v in subs_el]
    for opt in ("initial_condition", "dirichlet_nodes"):
        el = s.find(f"microenvironment_setup/options/{opt}")
        if el is not None and _true(el.get("enabled")):
            rep.add(f"microenvironment_setup.options.{opt}", "dropped", "field set from a MATLAB file")
    cell_types = [_import_cell_type(cd, rep, subs) for cd in s.cell_definitions()]
    type_names = [c["name"] for c in cell_types]

    rules: list[dict[str, Any]] = []
    enabled, rules_path = s.ruleset()
    if enabled and rules_path is not None:
        rows, skipped = read_rules_csv(project / rules_path)
        from ...ir.vocabulary import is_behaviour, is_signal

        for r in rows:
            if (
                r.cell_type in type_names
                and is_signal(r.signal, subs, type_names)
                and is_behaviour(r.behaviour, subs, type_names)
            ):
                rules.append(
                    {
                        "cell_type": r.cell_type,
                        "signal": r.signal,
                        "direction": r.direction,
                        "behaviour": r.behaviour,
                        "saturation_value": r.saturation_value,
                        "half_max": r.half_max,
                        "hill_power": r.hill_power,
                        "apply_to_dead": r.apply_to_dead,
                    }
                )
            else:
                rep.add(
                    f"rules: {r.cell_type},{r.signal},{r.behaviour}",
                    "dropped",
                    "names a type, signal or behaviour this model does not define",
                )
        rep.add("rules", "exact", f"{len(rules)} rules (CBHG v3)")
        for sk in skipped:
            if sk.reason != "comment":
                rep.add(f"rules: line {sk.line}", "dropped", sk.reason)

    placements: list[dict[str, Any]] = []
    pos_on, pos_path = s.cell_positions()
    if pos_on and pos_path is not None:
        src = project / pos_path
        rel = f"{project.name}_cells.csv"
        if out_dir is not None:
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, Path(out_dir) / rel)
        else:
            rel = str(src.resolve())
        placements.append({"kind": "positions_csv", "path": rel})
        header = src.read_text(encoding="utf-8").splitlines()[0].split(",")
        if len(header) > 4:
            rep.add(
                "initial_conditions.cells_csv",
                "approximated",
                f"positions and types kept; per-cell columns {header[4:]} are dropped",
            )
        else:
            rep.add("initial_conditions.cells_csv", "exact")

    intracellular: list[dict[str, Any]] = []
    for cd in s.cell_definitions():
        ic = cd.find("phenotype/intracellular")
        if ic is None:
            continue
        kind = ic.get("type", "")
        files: dict[str, str] = {}
        for tag in ("bnd_filename", "cfg_filename", "sbml_filename"):
            text = (ic.findtext(tag) or "").strip()
            if text:
                files[tag.removesuffix("_filename")] = text
        if out_dir is not None:
            for key, rel_path in list(files.items()):
                dest = Path(out_dir) / Path(rel_path).name
                shutil.copyfile(project / rel_path, dest)
                files[key] = dest.name
        fmt = {"maboss": "maboss", "sbml": "sbml", "roadrunner": "sbml"}.get(kind)
        if fmt is None:
            rep.add(f"cell_types[{cd.get('name')}].intracellular", "dropped", f"intracellular type {kind!r}")
            continue
        settings = {c.tag: (c.text or "").strip() for c in ic.iterfind("settings/*") if (c.text or "").strip()}
        intracellular.append({"cell_type": cd.get("name", ""), "format": fmt, "files": files, "settings": settings})
        rep.add(
            f"cell_types[{cd.get('name')}].intracellular",
            "approximated",
            "model files and settings kept; input and output mappings are carried as settings only",
        )

    native: list[dict[str, Any]] = []
    custom_dir = project / "custom_modules"
    if custom_dir.is_dir():
        files_c = sorted(p for p in custom_dir.iterdir() if p.is_file() and p.suffix in (".cpp", ".h", ".hpp"))
        same = all(TEMPLATE_CUSTOM.get(p.name) == _sha(p) for p in files_c) and files_c
        params = s.user_parameters()
        desc = (
            "the PhysiCell 1.14.2 template's custom code: places cells at random from user parameters and "
            "loads the cells CSV"
            if same
            else "custom C++ in custom_modules; not translated"
        )
        if params:
            desc += "; user parameters: " + ", ".join(f"{k}={v}" for k, (_, v) in sorted(params.items()))
        native.append(
            {
                "engine": "physicell",
                "kind": "custom_cpp",
                "files": [f"custom_modules/{p.name}" for p in files_c],
                "description": desc,
                "affects": ["initial_conditions"] if same else ["unknown"],
            }
        )
        rep.add("custom_modules", "native", desc)

    exp = {
        "max_time": s.get_float("overall/max_time"),
        **s.time_steps(),
        "save_interval": s.get_float("save/full_data/interval"),
        "threads": int(float(s.get("parallel/omp_num_threads", "1") or 1)),
    }
    doc = {
        "schema_version": "0.1",
        "name": project.name,
        "description": f"Imported from the PhysiCell project {project.name}.",
        "space": space,
        "substrates": substrates,
        "cell_types": cell_types,
        "rules": rules,
        "intracellular": intracellular,
        "initial_conditions": {"placements": placements},
        "observables": default_observables(s),
        "experiment": exp,
        "provenance": {
            "source": "PhysiCell project",
            "derived_from": {"engine": "physicell", "path": str(project.resolve()), "sha256": _sha(settings_path)},
            "created_with": f"abmfact {__version__}",
        },
        "native_extensions": native,
    }
    model = model_from_dict(doc, source=str(project), source_dir=Path(out_dir).resolve() if out_dir else None)
    return model, rep


# ---------------------------------------------------------------------------------------------------------
# Export


def _set(el: ET.Element, path: str, value: Any) -> None:
    node = el.find(path)
    if node is None:
        node = el
        for part in path.split("/"):
            child = node.find(part)
            node = child if child is not None else ET.SubElement(node, part)
    node.text = repr(float(value)) if isinstance(value, float) else str(value)


def _named_list(parent: ET.Element, tag: str, values: dict[str, float], names: list[str], default: float) -> None:
    template = parent.find(tag)
    for child in list(parent):
        parent.remove(child)
    for name in names:
        el = copy.deepcopy(template) if template is not None else ET.Element(tag)
        el.set("name", name)
        el.text = repr(float(values.get(name, default)))
        parent.append(el)


def _export_cell_type(proto: ET.Element, ct: Any, idx: int, model: IRModel, rep: FidelityReport) -> ET.Element:
    cd = copy.deepcopy(proto)
    cd.set("name", ct.name)
    cd.set("ID", str(idx))
    ph = cd.find("phenotype")
    assert ph is not None
    base = f"cell_types[{ct.name}]"
    names = [c.name for c in model.cell_types]
    # A model without substrates is exported with the template's inert substrate, which every reference to a
    # substrate in a cell definition must then name; PhysiCell refuses an empty or unknown substrate.
    subs = [s.name for s in model.substrates] or [INERT_SUBSTRATE]
    cyc = ph.find("cycle")
    assert cyc is not None
    code = next((c for c, m in CYCLE_CODES.items() if m == ct.cycle.model), None)
    if code is None:
        rep.add(f"{base}.cycle", "approximated", "custom cycle exported as the live model with its division rate")
        code, model_name, rates, durations = 5, "live", [ct.cycle.division_rate], None
    else:
        model_name, rates, durations = ct.cycle.model, ct.cycle.rates, ct.cycle.durations
        rep.add(f"{base}.cycle", "exact", model_name)
    for child in list(cyc):
        cyc.remove(child)
    cyc.set("code", str(code))
    cyc.set("name", CYCLE_NAMES.get(model_name, model_name))
    if durations:
        pd = ET.SubElement(cyc, "phase_durations", units="min")
        for i, d in enumerate(durations):
            ET.SubElement(pd, "duration", index=str(i), fixed_duration="false").text = repr(float(d))
    else:
        pr = ET.SubElement(cyc, "phase_transition_rates", units="1/min")
        rates = rates or [0.0]
        for i, r in enumerate(rates):
            end = 0 if i == len(rates) - 1 else i + 1
            ET.SubElement(pr, "rate", start_index=str(i), end_index=str(end), fixed_duration="false").text = repr(
                float(r)
            )
    for m in ph.iterfind("death/model"):
        if m.get("code") == "100":
            _set(m, "death_rate", ct.death.apoptosis_rate)
            _set(m, "phase_durations/duration", ct.death.apoptosis_duration)
        elif m.get("code") == "101":
            _set(m, "death_rate", ct.death.necrosis_rate)
            durs = m.findall("phase_durations/duration")
            if durs:
                durs[-1].text = repr(float(ct.death.necrosis_duration))
    rep.add(f"{base}.death", "exact", "rates and clearance times; volume changes use PhysiCell's defaults")
    _set(ph, "volume/total", ct.volume)
    rep.add(f"{base}.volume", "approximated", "total volume set; nuclear volume and growth keep template values")
    _set(ph, "mechanics/cell_cell_adhesion_strength", ct.mechanics.adhesion_strength)
    _set(ph, "mechanics/cell_cell_repulsion_strength", ct.mechanics.repulsion_strength)
    _set(ph, "mechanics/relative_maximum_adhesion_distance", ct.mechanics.max_adhesion_distance)
    aff = ph.find("mechanics/cell_adhesion_affinities")
    if aff is not None:
        _named_list(aff, "cell_adhesion_affinity", ct.mechanics.adhesion_affinities, names, 1.0)
    rep.add(f"{base}.mechanics", "exact")
    mot = ct.motility
    _set(ph, "motility/speed", mot.speed)
    _set(ph, "motility/persistence_time", mot.persistence_time)
    _set(ph, "motility/migration_bias", mot.migration_bias)
    _set(ph, "motility/options/enabled", "true" if mot.speed > 0 else "false")
    _set(ph, "motility/options/use_2D", "true" if model.space.dimensions == 2 else "false")
    if mot.chemotaxis is not None:
        _set(ph, "motility/options/chemotaxis/enabled", "true")
        _set(ph, "motility/options/chemotaxis/substrate", mot.chemotaxis.substrate)
        _set(ph, "motility/options/chemotaxis/direction", mot.chemotaxis.direction)
    else:
        _set(ph, "motility/options/chemotaxis/enabled", "false")
        _set(ph, "motility/options/chemotaxis/substrate", subs[0])
    sens = ph.find("motility/options/advanced_chemotaxis/chemotactic_sensitivities")
    if sens is not None:
        proto_s = sens.find("chemotactic_sensitivity")
        for child in list(sens):
            sens.remove(child)
        for s in subs:
            el = copy.deepcopy(proto_s) if proto_s is not None else ET.Element("chemotactic_sensitivity")
            el.set("substrate", s)
            el.text = "0.0"
            sens.append(el)
    rep.add(f"{base}.motility", "exact")
    sec = ph.find("secretion")
    assert sec is not None
    proto_sub = sec.find("substrate")
    for child in list(sec):
        sec.remove(child)
    by_sub = {x.substrate: x for x in ct.secretion}
    for s in subs:
        el = copy.deepcopy(proto_sub) if proto_sub is not None else ET.Element("substrate")
        el.set("name", s)
        x = by_sub.get(s)
        _set(el, "secretion_rate", x.secretion_rate if x else 0.0)
        _set(el, "secretion_target", x.target if x else 1.0)
        _set(el, "uptake_rate", x.uptake_rate if x else 0.0)
        _set(el, "net_export_rate", x.net_export_rate if x else 0.0)
        sec.append(el)
    rep.add(f"{base}.secretion", "exact")
    it = ct.interactions
    for tag, values in (
        ("live_phagocytosis_rates", it.phagocytosis_rates),
        ("attack_rates", it.attack_rates),
        ("fusion_rates", it.fusion_rates),
    ):
        group = ph.find(f"cell_interactions/{tag}")
        if group is not None:
            _named_list(group, group[0].tag if len(group) else tag[:-1], values, names, 0.0)
    for tag in ("apoptotic_phagocytosis_rate", "necrotic_phagocytosis_rate", "other_dead_phagocytosis_rate"):
        _set(ph, f"cell_interactions/{tag}", it.dead_phagocytosis_rate)
    _set(ph, "cell_interactions/attack_damage_rate", it.damage_rate)
    _set(ph, "cell_interactions/attack_duration", it.attack_duration)
    if any(v != 1.0 for v in it.immunogenicities.values()):
        rep.add(
            f"{base}.interactions.immunogenicities",
            "dropped",
            "the PhysiCell 1.14.2 settings file has no immunogenicity; set it with an 'immunogenicity to' rule",
        )
    _set(ph, "cell_integrity/damage_rate", ct.integrity.damage_rate)
    _set(ph, "cell_integrity/damage_repair_rate", ct.integrity.damage_repair_rate)
    rep.add(f"{base}.integrity", "exact")
    tr = ph.find("cell_transformations/transformation_rates")
    if tr is not None:
        _named_list(tr, "transformation_rate", ct.transformations, names, 0.0)
    rep.add(f"{base}.interactions", "exact")
    custom = cd.find("custom_data")
    if custom is not None:
        for child in list(custom):
            custom.remove(child)
        for k, v in ct.custom.items():
            ET.SubElement(custom, k, conserved="false", units="dimensionless", description="").text = repr(float(v))
    ipd = cd.find("initial_parameter_distributions")
    if ipd is not None:
        ipd.set("enabled", "false")
    return cd


def _sample_positions(model: IRModel, rep: FidelityReport) -> list[tuple[float, float, float, str]]:
    """Initial cells as positions. Random placements are drawn once, with seed 0."""
    rng = np.random.default_rng(0)
    rows: list[tuple[float, float, float, str]] = []
    random_kinds = False
    b = model.space.bounds
    for p in model.initial_conditions.placements:
        if p.kind == "positions":
            rows += [(x, y, 0.0, str(p.cell_type)) for x, y in p.positions or []]
            continue
        if p.kind == "positions_csv":
            pts, names = read_cells_csv(model.resolve(str(p.path)), [c.name for c in model.cell_types])
            rows += [(float(x), float(y), float(z), n) for (x, y, z), n in zip(pts, names, strict=True)]
            continue
        random_kinds = True
        if p.kind == "disc":
            area = math.pi * float(p.radius or 0) ** 2
        elif p.kind == "rectangle":
            area = float(np.prod(p.size or (0, 0)))
        else:
            area = (b.x_max - b.x_min) * (b.y_max - b.y_min)
        n = int(p.count) if p.count is not None else round(float(p.density or 0) * area)
        cx, cy = p.centre
        if p.kind == "disc":
            r = float(p.radius or 0) * np.sqrt(rng.random(n))
            t = 2 * math.pi * rng.random(n)
            xs, ys = cx + r * np.cos(t), cy + r * np.sin(t)
        elif p.kind == "rectangle":
            w, h = p.size or (0.0, 0.0)
            xs, ys = cx + (rng.random(n) - 0.5) * w, cy + (rng.random(n) - 0.5) * h
        else:
            xs = b.x_min + rng.random(n) * (b.x_max - b.x_min)
            ys = b.y_min + rng.random(n) * (b.y_max - b.y_min)
        rows += [(float(x), float(y), 0.0, str(p.cell_type)) for x, y in zip(xs, ys, strict=True)]
    if random_kinds:
        rep.add(
            "initial_conditions.placements",
            "approximated",
            "random placements are drawn once (seed 0) into cells.csv, so every seed starts from them",
        )
    elif rows:
        rep.add("initial_conditions.placements", "exact")
    return rows


def export_project(model: IRModel, out: str | Path, base: str | Path | None = None) -> FidelityReport:
    """Write ``model`` as a PhysiCell project in ``out``: the C++ of ``base`` (default: PhysiCell's
    template) plus ``config/PhysiCell_settings.xml``, ``config/cell_rules.csv`` and ``config/cells.csv``."""
    out = Path(out)
    base = Path(base) if base else TEMPLATE
    rep = FidelityReport("abm-ir", f"physicell:{out.name}", tier="T1")
    out.mkdir(parents=True, exist_ok=True)
    for f in PROJECT_FILES:
        shutil.copy2(base / f, out / f)
    for d in PROJECT_DIRS:
        if (base / d).is_dir():
            if (out / d).exists():
                shutil.rmtree(out / d)
            shutil.copytree(base / d, out / d)
    (out / "config").mkdir(exist_ok=True)
    s = SettingsXML.read(TEMPLATE / SETTINGS)
    root = s.root
    b = model.space.bounds
    for k in ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max"):
        s.set(f"domain/{k}", getattr(b, k))
    for k in ("dx", "dy", "dz"):
        s.set(f"domain/{k}", getattr(model.space.voxel, k))
    s.set("domain/use_2D", "true" if model.space.dimensions == 2 else "false")
    rep.add(
        "space",
        "exact" if model.space.boundary == "closed" else "approximated",
        "" if model.space.boundary == "closed" else "PhysiCell has no periodic domain; exported with walls",
    )
    e = model.experiment
    s.set("overall/max_time", e.max_time)
    dt_diffusion = min(e.dt_diffusion, e.dt_mechanics)
    notes = [DT_ORDER_NOTE] if dt_diffusion < e.dt_diffusion else []
    vox = model.space.voxel
    h = min(vox.dx, vox.dy, vox.dz) if model.space.dimensions == 3 else min(vox.dx, vox.dy)
    d_max = max((x.diffusion_coefficient for x in model.substrates), default=0.0)
    number = dt_diffusion * d_max / h**2
    if number > MAX_DIFFUSION_NUMBER:
        # A whole number of diffusion steps per mechanics step, as PhysiCell expects.
        dt_diffusion /= math.ceil(number / MAX_DIFFUSION_NUMBER)
        notes.append(
            f"dt_diffusion lowered to {dt_diffusion:.4g} min: dt*D/dx^2 was {number:.3g} for the fastest field, and "
            f"BioFVM's split diffusion solver is far from converged above {MAX_DIFFUSION_NUMBER:g}"
        )
    s.set("overall/dt_diffusion", dt_diffusion)
    s.set("overall/dt_mechanics", e.dt_mechanics)
    s.set("overall/dt_phenotype", e.dt_phenotype)
    s.set("parallel/omp_num_threads", e.threads)
    s.set_output("output", e.save_interval, svg=False)
    if notes:
        rep.add("experiment.dt_diffusion", "approximated", "; ".join(notes))
    rep.add("experiment", "exact", "end time, time steps, save interval and threads")

    me = root.find("microenvironment_setup")
    assert me is not None
    proto_v = me.find("variable")
    assert proto_v is not None
    for v in me.findall("variable"):
        me.remove(v)
    for i, sub in enumerate(model.substrates):
        v = copy.deepcopy(proto_v)
        v.set("name", sub.name)
        v.set("units", sub.units)
        v.set("ID", str(i))
        _set(v, "physical_parameter_set/diffusion_coefficient", sub.diffusion_coefficient)
        _set(v, "physical_parameter_set/decay_rate", sub.decay_rate)
        _set(v, "initial_condition", sub.initial_value)
        bc = sub.boundary
        dbc = v.find("Dirichlet_boundary_condition")
        assert dbc is not None
        any_held = any(bc.value_at(side) is not None for side in SIDES.values())
        dbc.set("enabled", "True" if any_held else "False")
        dbc.text = repr(float(bc.value))
        for bv in v.iterfind("Dirichlet_options/boundary_value"):
            side = SIDES[bv.get("ID", "")]
            val = bc.value_at(side) if not (model.space.dimensions == 2 and side.startswith("z")) else None
            bv.set("enabled", "True" if val is not None else "False")
            bv.text = repr(float(val if val is not None else bc.value))
        me.insert(i, v)
        rep.add(f"substrates[{sub.name}]", "exact")
    plot = root.find("save/SVG/plot_substrate/substrate")
    if plot is not None and model.substrates:
        plot.text = model.substrates[0].name
    if not model.substrates:
        rep.add(
            "substrates", "approximated", f"PhysiCell needs a substrate; an inert one named {INERT_SUBSTRATE!r} is kept"
        )
        me.insert(0, proto_v)

    defs = root.find("cell_definitions")
    assert defs is not None
    proto_cd = defs.find("cell_definition")
    assert proto_cd is not None
    for cd in defs.findall("cell_definition"):
        defs.remove(cd)
    for i, ct in enumerate(model.cell_types):
        defs.append(_export_cell_type(proto_cd, ct, i, model, rep))

    rows = [
        RuleRow(
            r.cell_type,
            r.signal,
            r.direction,
            r.behaviour,
            r.saturation_value,
            r.half_max,
            r.hill_power,
            r.apply_to_dead,
        )
        for r in model.rules
    ]
    write_rules_csv(rows, out / "config" / "cell_rules.csv", comment=f"Exported from ABM-IR model {model.name}")
    rs = root.find("cell_rules/rulesets/ruleset")
    assert rs is not None
    rs.set("enabled", "true" if rows else "false")
    _set(rs, "folder", "./config")
    _set(rs, "filename", "cell_rules.csv")
    if rows:
        rep.add("rules", "exact", f"{len(rows)} rules (CBHG v3)")

    cells = _sample_positions(model, rep)
    with (out / "config" / "cells.csv").open("w", encoding="utf-8") as fh:
        fh.write("x,y,z,type\n")
        for x, y, z, t in cells:
            fh.write(f"{x!r},{y!r},{z!r},{t}\n")
    cp = root.find("initial_conditions/cell_positions")
    assert cp is not None
    cp.set("enabled", "true")
    _set(cp, "folder", "./config")
    _set(cp, "filename", "cells.csv")
    s.set_param("user_parameters.number_of_cells", 0)

    for ic in model.intracellular:
        cd = next(c for c in defs.iterfind("cell_definition") if c.get("name") == ic.cell_type)
        ph = cd.find("phenotype")
        assert ph is not None
        if ic.format != "maboss":
            rep.add(f"intracellular[{ic.cell_type}]", "dropped", f"{ic.format} export is not implemented")
            continue
        el = ET.SubElement(ph, "intracellular", type="maboss")
        for key in ("bnd", "cfg"):
            src = model.resolve(ic.files[key])
            shutil.copyfile(src, out / "config" / src.name)
            ET.SubElement(el, f"{key}_filename").text = f"./config/{src.name}"
        st = ET.SubElement(el, "settings")
        for k, v in ic.settings.items():
            ET.SubElement(st, k).text = str(v)
        rep.add(f"intracellular[{ic.cell_type}]", "approximated", "model files and settings; mappings are not exported")
    for i in range(len(model.events)):
        rep.add(f"events[{i}]", "dropped", "PhysiCell has no timed events without custom code")
    for ext in model.native_extensions:
        rep.add(
            f"native_extensions[{ext.engine}]",
            "native" if ext.engine == "physicell" else "dropped",
            "custom code is not exported; the base project's code is used",
        )
    if model.observables:
        rep.add("observables", "exact", "computed by ABMfact from PhysiCell's output")
    s.write(out / SETTINGS)
    return rep
