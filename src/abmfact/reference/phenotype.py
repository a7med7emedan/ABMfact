"""Hill-type rules and the behaviours and signals the reference engine supports.

Rules combine exactly as in PhysiCell 1.14.2 (``core/PhysiCell_rules.cpp``: ``parse_csv_rule_v3``,
``set_behavior_min_value``, ``set_behavior_max_value`` and ``Hypothesis_Rule::evaluate``; and
``multivariate_Hill_response_function`` in ``core/PhysiCell_basic_signaling.cpp``). For one behaviour of
one cell type with base value ``b0`` (from the cell type):

* every ``increases`` rule raises the maximum ``bM`` to its saturation value if higher and lowers the
  minimum ``bm`` to ``b0`` if lower; every ``decreases`` rule lowers ``bm`` to its saturation value and
  raises ``bM`` to ``b0``;
* the up-regulating signals combine as ``U = S / (1 + S)`` with ``S = sum (s_k / h_k)^n_k`` over the
  ``increases`` rules, and likewise ``D`` over the ``decreases`` rules;
* the behaviour is ``b = b_up + (bm - b_up) D`` with ``b_up = b0 + (bM - b0) U``.

A dead cell sees a signal of 0 for every rule that does not apply to dead cells, and when no rule
applies to it the behaviour is left unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np

from ..ir import CellType, IRModel

FIXED_SIGNALS = frozenset(
    {
        "pressure",
        "volume",
        "time",
        "current time",
        "global time",
        "dead",
        "is dead",
        "apoptotic",
        "is_apoptotic",
        "necrotic",
        "is_necrotic",
        "contact with live cell",
        "contact with live cells",
        "contact with dead cell",
        "contact with dead cells",
    }
)
FIXED_BEHAVIOURS = frozenset(
    {
        "cycle entry",
        "exit from cycle phase 0",
        "apoptosis",
        "necrosis",
        "migration speed",
        "migration bias",
        "migration persistence time",
        "cell-cell adhesion",
        "cell-cell repulsion",
    }
)
SUBSTRATE_SIGNAL_FORMS = ("{}", "{} gradient", "grad({})", "gradient of {}")
SUBSTRATE_BEHAVIOUR_FORMS = ("{} secretion", "{} secretion target", "{} uptake", "{} export")


def signal_supported(name: str, model: IRModel) -> bool:
    if name in FIXED_SIGNALS:
        return True
    subs = [s.name for s in model.substrates]
    types = [c.name for c in model.cell_types]
    return any(name == f.format(s) for f in SUBSTRATE_SIGNAL_FORMS for s in subs) or any(
        name == f"contact with {t}" for t in types
    )


def behaviour_supported(name: str, model: IRModel) -> bool:
    if name in FIXED_BEHAVIOURS:
        return True
    subs = [s.name for s in model.substrates]
    return any(name == f.format(s) for f in SUBSTRATE_BEHAVIOUR_FORMS for s in subs)


def base_value(ct: CellType, behaviour: str) -> float:
    """The behaviour's value as the cell type defines it, before any rule."""
    if behaviour in ("cycle entry", "exit from cycle phase 0"):
        return float((ct.cycle.rates or [ct.cycle.division_rate])[0])
    simple = {
        "apoptosis": ct.death.apoptosis_rate,
        "necrosis": ct.death.necrosis_rate,
        "migration speed": ct.motility.speed,
        "migration bias": ct.motility.migration_bias,
        "migration persistence time": ct.motility.persistence_time,
        "cell-cell adhesion": ct.mechanics.adhesion_strength,
        "cell-cell repulsion": ct.mechanics.repulsion_strength,
    }
    if behaviour in simple:
        return float(simple[behaviour])
    for sec in ct.secretion:
        for form, attr in (
            ("{} secretion target", "target"),
            ("{} secretion", "secretion_rate"),
            ("{} uptake", "uptake_rate"),
            ("{} export", "net_export_rate"),
        ):
            if behaviour == form.format(sec.substrate):
                return float(getattr(sec, attr))
    if behaviour.endswith(" secretion target"):
        return 1.0
    return 0.0


@dataclass
class Term:
    signal: str
    half_max: float
    hill_power: float
    apply_to_dead: bool


@dataclass
class Ruleset:
    """All rules acting on one behaviour of one cell type."""

    behaviour: str
    base: float
    min_value: float = 9e99
    max_value: float = -9e99
    up: list[Term] = field(default_factory=list)
    down: list[Term] = field(default_factory=list)

    @property
    def signals(self) -> list[str]:
        return [t.signal for t in self.up + self.down]

    def evaluate(self, signals: Mapping[str, np.ndarray], dead: np.ndarray, current: np.ndarray) -> np.ndarray:
        """The behaviour for every cell. Cells to which no rule applies keep ``current``."""
        dead = np.asarray(dead, dtype=bool)

        def hill(terms: list[Term]) -> tuple[np.ndarray, np.ndarray]:
            total = np.zeros(dead.shape)
            applies = np.zeros(dead.shape, dtype=bool)
            for t in terms:
                active = t.apply_to_dead | ~dead
                s = np.where(active, signals[t.signal], 0.0)
                total += (np.maximum(s, 0.0) / t.half_max) ** t.hill_power
                applies |= active
            return total / (1.0 + total), applies

        hu, a_up = hill(self.up)
        hd, a_down = hill(self.down)
        b_up = self.base + (self.max_value - self.base) * hu if self.up else np.full(dead.shape, self.base)
        out = b_up + (self.min_value - b_up) * hd if self.down else b_up
        return np.where(a_up | a_down, out, current)


def build_rulesets(model: IRModel) -> dict[tuple[str, str], Ruleset]:
    """Rulesets keyed by (cell type, behaviour), with minima and maxima set as PhysiCell sets them."""
    out: dict[tuple[str, str], Ruleset] = {}
    for r in model.rules:
        key = (r.cell_type, r.behaviour)
        if key not in out:
            out[key] = Ruleset(r.behaviour, base_value(model.cell_type(r.cell_type), r.behaviour))
        rs = out[key]
        term = Term(r.signal, r.half_max, r.hill_power, r.apply_to_dead)
        if r.direction == "increases":
            rs.up.append(term)
            rs.min_value = min(rs.min_value, rs.base)
            rs.max_value = max(rs.max_value, r.saturation_value)
        else:
            rs.down.append(term)
            rs.min_value = min(rs.min_value, r.saturation_value)
            rs.max_value = max(rs.max_value, rs.base)
    return out
