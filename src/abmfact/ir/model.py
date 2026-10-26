"""ABM-IR v0.1: one description of a biological agent-based model that every engine can read.

The vocabulary follows PhysiCell, so a declarative PhysiCell project maps onto it closely, but nothing
here is specific to one engine. Units are those named in ``units`` (micron and minute by default, as in
PhysiCell). Engine-specific code that cannot be described declaratively is listed under
``native_extensions`` and is never translated.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from .vocabulary import is_behaviour, is_signal

SCHEMA_VERSION = "0.1"

NonNeg = Annotated[float, Field(ge=0)]
Pos = Annotated[float, Field(gt=0)]
Name = Annotated[str, Field(min_length=1, pattern=r"^[^,\n]+$")]
Side = Literal["x_min", "x_max", "y_min", "y_max", "z_min", "z_max"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Units(_Base):
    """The units of length and time that the model's quantities are given in."""

    space: str = "micron"
    time: str = "min"


class Bounds(_Base):
    """The extent of the domain along each axis. Each minimum must be below its maximum."""

    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float = -10.0
    z_max: float = 10.0

    @model_validator(mode="after")
    def _ordered(self) -> Bounds:
        for axis in "xyz":
            if getattr(self, f"{axis}_min") >= getattr(self, f"{axis}_max"):
                raise ValueError(f"{axis}_min must be below {axis}_max")
        return self


class Voxel(_Base):
    """The size of one voxel of the substrate grid. In a 2D model ``dz`` still sets the voxel volume."""

    dx: Pos
    dy: Pos
    dz: Pos = 20.0


class Space(_Base):
    """The simulation domain: its dimension, bounds, substrate grid, and whether it is closed or periodic."""

    dimensions: Literal[2, 3] = 2
    bounds: Bounds
    voxel: Voxel
    boundary: Literal["closed", "periodic"] = "closed"


class SubstrateBoundary(_Base):
    """``dirichlet`` holds every side at ``value``; ``sides`` overrides one side, ``None`` makes it no-flux."""

    kind: Literal["no_flux", "dirichlet"] = "no_flux"
    value: NonNeg = 0.0
    sides: dict[Side, NonNeg | None] = Field(default_factory=dict)

    def value_at(self, side: str) -> float | None:
        """The Dirichlet value on one side, or ``None`` for no flux."""
        if side in self.sides:
            return self.sides[side]  # type: ignore[index,unused-ignore]
        return self.value if self.kind == "dirichlet" else None


class Substrate(_Base):
    """A diffusing substance, with its diffusion coefficient, decay rate, initial value and boundary."""

    name: Name
    units: str = "dimensionless"
    diffusion_coefficient: NonNeg
    decay_rate: NonNeg = 0.0
    initial_value: NonNeg = 0.0
    boundary: SubstrateBoundary = Field(default_factory=SubstrateBoundary)


class Cycle(_Base):
    """A cell cycle. ``live`` has one phase whose exit rate is the division rate.

    Give either ``rates`` (phase exit rates, per time unit, in phase order) or ``durations`` (fixed phase
    durations), as PhysiCell does.
    """

    model: Literal[
        "live",
        "flow_cytometry",
        "flow_cytometry_separated",
        "ki67_basic",
        "ki67_advanced",
        "cycling_quiescent",
        "custom",
    ] = "live"
    rates: list[NonNeg] | None = None
    durations: list[Pos] | None = None

    @model_validator(mode="after")
    def _one_form(self) -> Cycle:
        if self.rates is not None and self.durations is not None:
            raise ValueError("give either rates or durations, not both")
        if self.rates is None and self.durations is None:
            self.rates = [0.0]
        return self

    @property
    def division_rate(self) -> float:
        """Total rate of completing the cycle, for a cycle read as phases in series."""
        times = [1.0 / r if r > 0 else math.inf for r in self.rates] if self.rates else list(self.durations or [])
        total = sum(times)
        return 0.0 if total == 0 or math.isinf(total) else 1.0 / total


class Death(_Base):
    """Apoptosis and necrosis rates, and how long a dead cell remains before it is removed."""

    apoptosis_rate: NonNeg = 0.0
    necrosis_rate: NonNeg = 0.0
    apoptosis_duration: Pos = 516.0
    necrosis_duration: Pos = 86400.0


class Integrity(_Base):
    """Damage a cell carries, as in PhysiCell's cell integrity: it grows at ``damage_rate`` and is repaired
    at ``damage_repair_rate``, both per unit time. Attacks add to it, and rules can read it as the
    ``damage`` signal."""

    damage_rate: NonNeg = 0.0
    damage_repair_rate: NonNeg = 0.0


class Mechanics(_Base):
    """Centre-based mechanics. ``max_adhesion_distance`` is relative to the cell radius, as in PhysiCell."""

    adhesion_strength: NonNeg = 0.4
    repulsion_strength: NonNeg = 10.0
    max_adhesion_distance: Annotated[float, Field(ge=1.0)] = 1.25
    adhesion_affinities: dict[str, NonNeg] = Field(default_factory=dict)


class Chemotaxis(_Base):
    """Migration bias along one substrate's gradient: ``direction`` 1 moves up the gradient, -1 down it."""

    substrate: Name
    direction: Literal[1, -1] = 1


class Motility(_Base):
    """Cell migration, as in PhysiCell: speed, persistence time and migration bias.

    ``migration_bias`` runs from 0 (a random direction) to 1 (the chemotaxis direction only).
    """

    speed: NonNeg = 0.0
    persistence_time: Pos = 1.0
    migration_bias: Annotated[float, Field(ge=0, le=1)] = 0.0
    chemotaxis: Chemotaxis | None = None


class Secretion(_Base):
    """Exchange of one substrate between a cell and its voxel, as in PhysiCell.

    The cell secretes towards the ``target`` density at ``secretion_rate``, takes up at ``uptake_rate``,
    and adds ``net_export_rate`` of substrate per time unit.
    """

    substrate: Name
    secretion_rate: NonNeg = 0.0
    target: NonNeg = 1.0
    uptake_rate: NonNeg = 0.0
    net_export_rate: float = 0.0


class Interactions(_Base):
    """Contact interactions with other cells, as in PhysiCell.

    Attack, phagocytosis and fusion rates are keyed by the name of the target cell type. ``damage_rate`` is
    the attack damage rate, and ``dead_phagocytosis_rate`` applies to dead cells of any type. An attack
    lasts ``attack_duration`` on average. ``immunogenicities`` scales how readily each attacker type starts
    an attack on this cell type, keyed by the attacker's name; a missing type counts as 1.
    """

    attack_rates: dict[str, NonNeg] = Field(default_factory=dict)
    damage_rate: NonNeg = 0.0
    attack_duration: Pos = 0.1
    immunogenicities: dict[str, NonNeg] = Field(default_factory=dict)
    phagocytosis_rates: dict[str, NonNeg] = Field(default_factory=dict)
    dead_phagocytosis_rate: NonNeg = 0.0
    fusion_rates: dict[str, NonNeg] = Field(default_factory=dict)

    @property
    def active(self) -> bool:
        """Whether any attack, phagocytosis, fusion or dead phagocytosis rate is above zero."""
        rates = [*self.attack_rates.values(), *self.phagocytosis_rates.values(), *self.fusion_rates.values()]
        return any(r > 0 for r in rates) or self.dead_phagocytosis_rate > 0


class CellType(_Base):
    """One cell type: its volume, cycle, death, mechanics, motility, secretion and interactions.

    ``transformations`` maps other cell type names to the rate of turning into that type. ``custom`` holds
    named numeric custom data.
    """

    name: Name
    volume: Pos = 2494.0
    cycle: Cycle = Field(default_factory=Cycle)
    death: Death = Field(default_factory=Death)
    integrity: Integrity = Field(default_factory=Integrity)
    mechanics: Mechanics = Field(default_factory=Mechanics)
    motility: Motility = Field(default_factory=Motility)
    secretion: list[Secretion] = Field(default_factory=list)
    interactions: Interactions = Field(default_factory=Interactions)
    transformations: dict[str, NonNeg] = Field(default_factory=dict)
    custom: dict[str, float] = Field(default_factory=dict)

    @property
    def radius(self) -> float:
        """Radius of a sphere with this volume."""
        return float((3.0 * self.volume / (4.0 * math.pi)) ** (1.0 / 3.0))


class Rule(_Base):
    """One signal-to-behaviour rule in PhysiCell's cell behaviour grammar (CBHG, rules CSV version 3).

    ``saturation_value`` is the behaviour value when the signal saturates: the maximum for an
    ``increases`` rule and the minimum for a ``decreases`` rule. The base value comes from the cell type.
    """

    cell_type: Name
    signal: Name
    direction: Literal["increases", "decreases"]
    behaviour: Name = Field(validation_alias=AliasChoices("behaviour", "behavior"))
    saturation_value: NonNeg
    half_max: Pos
    hill_power: Pos
    apply_to_dead: bool = False


class IntracellularModel(_Base):
    """A reference to an intracellular model file. Paths are relative to the IR file."""

    cell_type: Name
    format: Literal["maboss", "sbml", "sbml-qual"]
    files: dict[str, str]
    inputs: dict[str, str] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=dict)
    time_step: Pos | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class Placement(_Base):
    """Initial cells. ``density`` is cells per unit area (2D) or volume (3D); ``positions`` are explicit."""

    kind: Literal["disc", "rectangle", "random", "positions", "positions_csv"]
    cell_type: Name | None = None
    count: Annotated[int, Field(ge=0)] | None = None
    density: NonNeg | None = None
    centre: tuple[float, float] = (0.0, 0.0)
    radius: Pos | None = None
    size: tuple[Pos, Pos] | None = None
    positions: list[tuple[float, float]] | None = None
    path: str | None = None

    @model_validator(mode="after")
    def _complete(self) -> Placement:
        if self.kind == "positions_csv":
            if not self.path:
                raise ValueError("positions_csv needs path")
            return self
        if self.cell_type is None:
            raise ValueError(f"{self.kind} needs cell_type")
        if self.kind == "positions":
            if not self.positions:
                raise ValueError("positions needs a non-empty positions list")
            return self
        if (self.count is None) == (self.density is None):
            raise ValueError(f"{self.kind} needs exactly one of count or density")
        if self.kind == "disc" and self.radius is None:
            raise ValueError("disc needs radius")
        if self.kind == "rectangle" and self.size is None:
            raise ValueError("rectangle needs size")
        return self


class InitialConditions(_Base):
    """The cells present at the start of a run, as a list of placements."""

    placements: list[Placement] = Field(default_factory=list)


class TimedEvent(_Base):
    """A change at a set time, held until ``until`` when given (then reverted), for example a drug pulse."""

    time: NonNeg
    action: Literal["set_substrate_boundary", "set_substrate", "add_cells", "set_parameter"]
    target: Name
    value: float | None = None
    until: NonNeg | None = None

    @model_validator(mode="after")
    def _window(self) -> TimedEvent:
        if self.until is not None and self.until <= self.time:
            raise ValueError("until must be after time")
        return self


class Observable(_Base):
    """A named quantity measured at every saved time, such as a cell count or a substrate mean.

    ``cell_type`` restricts a count or position to one type. The ``substrate_`` kinds need ``substrate``,
    and ``type_fraction`` needs ``cell_type``.
    """

    name: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
    kind: Literal[
        "cell_count",
        "live_count",
        "dead_count",
        "type_fraction",
        "mean_position",
        "radius_of_gyration",
        "substrate_mean",
        "substrate_min",
        "substrate_max",
    ]
    cell_type: Name | None = None
    substrate: Name | None = None
    axis: Literal["x", "y", "z"] = "x"

    @model_validator(mode="after")
    def _needs(self) -> Observable:
        if self.kind.startswith("substrate_") and self.substrate is None:
            raise ValueError(f"{self.kind} needs substrate")
        if self.kind == "type_fraction" and self.cell_type is None:
            raise ValueError("type_fraction needs cell_type")
        return self


class Experiment(_Base):
    """The run length, the time steps and the save interval, in the model's time unit, as in PhysiCell.

    ``dt_diffusion``, ``dt_mechanics`` and ``dt_phenotype`` are the steps of each process. ``threads`` is
    the number of threads an engine may use.
    """

    max_time: Pos
    dt_diffusion: Pos = 0.01
    dt_mechanics: Pos = 0.1
    dt_phenotype: Pos = 6.0
    save_interval: Pos = 60.0
    threads: Annotated[int, Field(ge=1)] = 1


class DerivedFrom(_Base):
    """The engine project a model was imported from, with an optional SHA-256 digest of its source."""

    engine: str
    path: str
    sha256: str | None = None


class Provenance(_Base):
    """Where a model came from: its source, DOI, authors, licence, origin project and the tool that made it."""

    source: str | None = None
    doi: str | None = None
    authors: list[str] = Field(default_factory=list)
    licence: str | None = None
    derived_from: DerivedFrom | None = None
    created_with: str | None = None
    notes: str = ""


class NativeExtension(_Base):
    """Engine-specific code that the IR does not describe and never translates. ``affects`` names what it may change."""

    engine: str
    kind: Literal["custom_cpp", "custom_function", "other"]
    files: list[str] = Field(default_factory=list)
    description: str = ""
    affects: list[str] = Field(default_factory=list)


class IRModel(_Base):
    """An ABM-IR document."""

    schema_version: Literal["0.1"] = "0.1"
    name: Name
    description: str = ""
    units: Units = Field(default_factory=Units)
    space: Space
    substrates: list[Substrate] = Field(default_factory=list)
    cell_types: Annotated[list[CellType], Field(min_length=1)]
    rules: list[Rule] = Field(default_factory=list)
    intracellular: list[IntracellularModel] = Field(default_factory=list)
    initial_conditions: InitialConditions = Field(default_factory=InitialConditions)
    events: list[TimedEvent] = Field(default_factory=list)
    observables: list[Observable] = Field(default_factory=list)
    experiment: Experiment
    provenance: Provenance = Field(default_factory=Provenance)
    native_extensions: list[NativeExtension] = Field(default_factory=list)

    #: Folder that relative file paths in the model are resolved against (the IR file's folder).
    _source_dir: Path | None = PrivateAttr(default=None)

    @property
    def source_dir(self) -> Path | None:
        """The folder relative file paths are resolved against (the IR file's folder), or ``None``."""
        return self._source_dir

    def resolve(self, path: str) -> Path:
        """A file path from the model, made absolute against the IR file's folder."""
        p = Path(path)
        if p.is_absolute() or self._source_dir is None:
            return p
        return self._source_dir / p

    def cell_type(self, name: str) -> CellType:
        """The cell type called ``name``. Raises ``KeyError`` when the model has none."""
        for ct in self.cell_types:
            if ct.name == name:
                return ct
        raise KeyError(name)

    def substrate(self, name: str) -> Substrate:
        """The substrate called ``name``. Raises ``KeyError`` when the model has none."""
        for s in self.substrates:
            if s.name == name:
                return s
        raise KeyError(name)

    @model_validator(mode="after")
    def _references(self) -> IRModel:
        problems = reference_problems(self)
        if problems:
            raise ValueError("\n".join(f"{path}: {msg}" for path, msg in problems))
        return self


def _dupes(names: list[str]) -> set[str]:
    return {n for n in names if names.count(n) > 1}


def reference_problems(m: IRModel) -> list[tuple[str, str]]:
    """Every cross-reference that names something the model does not define, as (path, message)."""
    subs = [s.name for s in m.substrates]
    types = [c.name for c in m.cell_types]
    out: list[tuple[str, str]] = []
    for what, names in (("substrates", subs), ("cell_types", types), ("observables", [o.name for o in m.observables])):
        for d in sorted(_dupes(names)):
            out.append((what, f"duplicate name {d!r}"))

    def need(path: str, value: str | None, pool: list[str], kind: str) -> None:
        if value is not None and value not in pool:
            out.append((path, f"unknown {kind} {value!r}"))

    for ct in m.cell_types:
        base = f"cell_types[{ct.name}]"
        for i, sec in enumerate(ct.secretion):
            need(f"{base}.secretion[{i}].substrate", sec.substrate, subs, "substrate")
        if ct.motility.chemotaxis is not None:
            need(f"{base}.motility.chemotaxis.substrate", ct.motility.chemotaxis.substrate, subs, "substrate")
        for t in ct.mechanics.adhesion_affinities:
            need(f"{base}.mechanics.adhesion_affinities", t, types, "cell type")
        for field in ("attack_rates", "immunogenicities", "phagocytosis_rates", "fusion_rates"):
            for t in getattr(ct.interactions, field):
                need(f"{base}.interactions.{field}", t, types, "cell type")
        for t in ct.transformations:
            need(f"{base}.transformations", t, types, "cell type")
    for i, r in enumerate(m.rules):
        need(f"rules[{i}].cell_type", r.cell_type, types, "cell type")
        if not is_signal(r.signal, subs, types):
            out.append((f"rules[{i}].signal", f"{r.signal!r} is not a signal for this model's substrates and types"))
        if not is_behaviour(r.behaviour, subs, types):
            out.append((f"rules[{i}].behaviour", f"{r.behaviour!r} is not a behaviour for this model"))
    for i, ic in enumerate(m.intracellular):
        need(f"intracellular[{i}].cell_type", ic.cell_type, types, "cell type")
    for i, p in enumerate(m.initial_conditions.placements):
        need(f"initial_conditions.placements[{i}].cell_type", p.cell_type, types, "cell type")
    for i, ev in enumerate(m.events):
        if ev.action in ("set_substrate_boundary", "set_substrate"):
            need(f"events[{i}].target", ev.target, subs, "substrate")
        elif ev.action == "add_cells":
            need(f"events[{i}].target", ev.target, types, "cell type")
    for ob in m.observables:
        need(f"observables[{ob.name}].cell_type", ob.cell_type, types, "cell type")
        need(f"observables[{ob.name}].substrate", ob.substrate, subs, "substrate")
    if m.space.dimensions == 2 and any(o.axis == "z" for o in m.observables):
        out.append(("observables", "a 2D model has no z axis"))
    return out
